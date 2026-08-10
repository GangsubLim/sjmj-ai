"""재처리 승계 계획 — 옛 확정 쌍과 새 검출 행을 행 앵커로 짝짓는다(ADR 0007 · 0010).

DB도 파일도 모른다(spec 불변식 3). 입력이 정수와 문자열뿐이라 매칭 규칙 전체가
합성 데이터로 검증되며, 여기서 나온 계획을 worker/db.py가 실행만 한다.

⚠️ 모듈 레벨에 stdlib 밖 의존을 두지 않는다(handwriting/warp_gate.py와 동일 규약) —
   그래야 paddle-free 코어 venv에서도 `from handwriting.relink import plan_relink`가 성공한다.
"""

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

# 승계에 실패한 쌍의 배제 사유. exclusion_reason VARCHAR(32)에 값만 추가한다
# (migration 0건 — spec §1). blank_crop과 달리 "그림 자체가 없다"를 뜻한다(CONTEXT.md).
RELINK_FAILED = "relink_failed"


@dataclass(frozen=True)
class OldPair:
    """재처리 이전의 확정 학습쌍(training_pairs 1행에서 필요한 것만).

    앵커가 두 개인 이유는 두 값의 **출처가 다르기 때문**이다. `supply`는
    ocr_correction이 `final_supply`, 즉 사람이 확정한 값으로 적재하고, `draft_supply`는
    같은 행을 그때의 모델이 읽은 값이다(옛 result_json에서 온다). 새 쪽은 언제나 이번
    실행의 모델 인식값이라, final만 앵커로 쓰면 "행 구조가 바뀌었는가"가 아니라 "이번
    인식이 사람 정답과 일치하는가"를 재게 된다. 둘 다 허용해야 축이 맞는다.

    `draft_supply`는 확정 시점 스냅샷이다 — 공급자(worker/db.fetch_pairs)가
    training_pairs.draft_supply 컬럼을 그대로 싣는다. 옛 초안을 row_index로 조인하던 시절에는
    미결 쌍의 낡은 인덱스가 다른 행을 가리켜 가짜 앵커가 만들어졌고, 그것을 막으려 미결 쌍의
    draft를 버리면서 한 번 미결이 된 쌍이 영구히 회수 불가가 됐다(Issue #106). None은 "믿을 수
    있는 앵커가 없다" 하나를 뜻하며(미판독·정합 가드 탈락·범위 밖 격리를 구분하지 않는다),
    여기서는 None 앵커가 서로 절대 같지 않다는 것만으로 회수에서 빠진다.
    """

    pair_id: int
    row_index: int
    supply: int | None
    draft_supply: int | None = None


@dataclass(frozen=True)
class NewRow:
    """재처리로 새로 검출된 행(새 result_json의 rows 1개에서 필요한 것만)."""

    row_index: int
    supply: int | None


@dataclass(frozen=True)
class Relinked:
    """승계 대상 — 1단계에서 tmp_ref로 비우고 2단계에서 final_ref를 기입한다."""

    pair_id: int
    tmp_ref: str
    final_ref: str
    final_row_index: int


@dataclass(frozen=True)
class Orphaned:
    """미결 대상 — 확정 라벨은 남기고 좌표만 row- 네임스페이스 밖으로 뺀다."""

    pair_id: int
    orphan_ref: str


@dataclass(frozen=True)
class RelinkPlan:
    """한 잡의 승계 계획 전체. 실행(SQL)은 worker/db.py가 한다."""

    relinked: tuple[Relinked, ...]
    orphaned: tuple[Orphaned, ...]

    @property
    def should_release_gate(self) -> bool:
        """미결이 하나라도 나온 잡만 검수 게이트를 해제한다(ADR 0011).

        이 규칙은 현 단계의 결정이라 바뀔 수 있다 — 정책 판정을 여기 한 곳에 모아 두어
        고칠 자리가 하나가 되게 한다. 무조건 해제는 전량 재처리를 전량 재검수로 만들어
        재처리를 실행되지 않는 기능으로 되돌린다.
        """
        return bool(self.orphaned)


def row_ref(job_id: int, row_index: int) -> str:
    """행 좌표 — 뱅크 key 계약(is_crop_ref)을 통과하는 유일한 형식."""
    return f"job-{job_id}/row-{row_index}"


def tmp_ref(job_id: int, pair_id: int) -> str:
    """1단계 임시 좌표 — pair_id 기반이라 유일하고 is_crop_ref를 통과하지 못한다."""
    return f"job-{job_id}/tmp-{pair_id}"


def orphan_ref(job_id: int, pair_id: int) -> str:
    """미결 좌표 — 형식 위반 값이라 뱅크 진입이 구조적으로 불가능하다(§6)."""
    return f"job-{job_id}/orphan-{pair_id}"


def _anchor_seq(supplies: list[int | None], side: str) -> list[object]:
    """앵커 시퀀스를 만든다 — 금액 미인식(None)은 서로 절대 같지 않은 유일값으로 치환한다.

    None을 그대로 두면 `None == None`이라 앵커가 없는 두 줄이 서로를 짝지어 버린다(§4 ③).
    side로 옛/새를 가르므로 같은 위치의 None끼리도 매칭되지 않는다.
    """
    return [s if s is not None else (side, i) for i, s in enumerate(supplies)]


def _ambiguous_amounts(old: list[int | None], new: list[int | None]) -> set[int]:
    """옛·새 개수가 어긋난 금액값을 고른다 — 그 값을 가진 줄은 전량 미결로 민다(§4 ②).

    옛 3줄이 같은 금액인데 새로 2줄만 잡히면 어느 것이 살아남았는지 데이터에 답이 없다.
    개수가 같으면 순서가 답해주므로 게이트에 걸리지 않는다.
    """
    old_counts = Counter(s for s in old if s is not None)
    new_counts = Counter(s for s in new if s is not None)
    return {v for v in set(old_counts) | set(new_counts) if old_counts[v] != new_counts[v]}


def _lcs_match(old_anchor: list[object], new_anchor: list[object]) -> dict[int, int]:
    """순서를 보존하는 최장 공통 부분수열 매칭(옛 인덱스 → 새 인덱스).

    autojunk=False — 기본 휴리스틱은 원소가 200개를 넘을 때 흔한 값을 junk로 빼는데,
    여기서 흔한 값(반복 금액)은 버릴 대상이 아니라 그룹 게이트가 판정할 대상이다.
    """
    matcher = SequenceMatcher(a=old_anchor, b=new_anchor, autojunk=False)
    out: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            out[block.a + k] = block.b + k
    return out


def _gaps(matched: dict[int, int], old_count: int, new_count: int):
    """확정 앵커 사이의 빈칸을 (옛 인덱스 목록, 새 인덱스 목록)으로 끊어 낸다.

    회수(2단계)는 이 빈칸 안에서만 허용된다 — 빈칸을 넘어 짝을 지으면 LCS가 지킨 순서
    제약이 무너져, 확정 라벨이 전혀 다른 줄의 그림에 붙는다.
    """
    anchors = sorted(matched.items())
    bounds = [(-1, -1), *anchors, (old_count, new_count)]
    for (i_left, j_left), (i_right, j_right) in zip(bounds, bounds[1:], strict=False):
        olds = list(range(i_left + 1, i_right))
        news = list(range(j_left + 1, j_right))
        if olds and news:
            yield olds, news


def plan_relink(job_id: int, old_pairs: list[OldPair], new_rows: list[NewRow]) -> RelinkPlan:
    """옛 확정 쌍을 새 검출 행에 짝지어 승계·미결 계획을 만든다.

    행검출의 변화는 순서 보존 편집이다 — 줄이 추가·삭제될 뿐 물리적 순서가 섞이지 않는다.
    따라서 매칭은 최장 공통 부분수열이며 stdlib difflib으로 코어 규약을 지킨 채 구현한다.
    ocr_poc/score.py:align_rows는 선례지만 greedy first-match에 순서 제약이 없어 승계용으로
    쓰지 않는다(틀리면 오염된 학습쌍이 뱅크에 들어간다).

    **2단계인 이유(OldPair docstring 참조).** 옛 쪽 앵커는 사람이 확정한 금액이고 새 쪽은
    모델 인식값이라 축이 다르다. ①에서 확정 금액으로 못 박고, ②에서 남은 쌍을 옛 모델값
    (draft)으로 **①이 만든 빈칸 안에서만** 회수한다. ②가 없으면 금액을 교정했던 행이 전부
    미결이 되어, 같은 사진·같은 엔진으로 다시 돌려도 멱등이 깨지고(§9 복구 전제) 재처리가
    곧 전량 재검수가 된다.

    Args:
        job_id: 대상 OCR 잡 id(좌표 접두).
        old_pairs: 재처리 이전의 확정 쌍 전량.
        new_rows: 새 result_json의 행 전량. supply는 THOUSAND_MULT 적용 후 원 단위라
            training_pairs.supply(사람이 화면에 입력한 원 단위)와 자가 같다.

    Returns:
        승계·미결이 빠짐없이 담긴 RelinkPlan. relinked ∪ orphaned = old_pairs 전량이다.
    """
    olds = sorted(old_pairs, key=lambda p: (p.row_index, p.pair_id))
    news = sorted(new_rows, key=lambda r: r.row_index)
    old_supplies = [p.supply for p in olds]
    new_supplies = [r.supply for r in news]
    ambiguous = _ambiguous_amounts(old_supplies, new_supplies)

    # ① 확정 앵커 — 사람이 확정한 금액이 이번 인식과 일치하는 행을 먼저 못 박는다.
    matched: dict[int, int] = {}
    for i, j in _lcs_match(
        _anchor_seq(old_supplies, "old"), _anchor_seq(new_supplies, "new")
    ).items():
        if olds[i].supply in ambiguous:
            continue  # 정렬이 짝을 지었어도 개수가 어긋난 값은 전량 미결로 민다
        matched[i] = j

    # ② draft 회수 — ①에서 남은 쌍을 **확정 앵커 사이의 빈칸 안에서만** 옛 모델값으로
    #    다시 맞춘다. 사람이 금액을 고쳤던 행이 ①을 통과하지 못하는 것이 정상 케이스이고
    #    (두 앵커의 출처가 다르다), 그 행을 여기서 회수하지 않으면 승계 실패율이 행 검출
    #    변화율이 아니라 금액 인식 오류율을 따라가 재처리가 곧 전량 재검수가 된다.
    #    빈칸을 나누는 것이 순서 제약을 지키는 장치다 — 넘어가면 확정 라벨이 다른 줄에 붙는다.
    for gap_olds, gap_news in list(_gaps(matched, len(olds), len(news))):
        gap_old_supplies = [olds[i].draft_supply for i in gap_olds]
        gap_new_supplies = [news[j].supply for j in gap_news]
        gap_ambiguous = _ambiguous_amounts(gap_old_supplies, gap_new_supplies)
        for a, b in _lcs_match(
            _anchor_seq(gap_old_supplies, "old"), _anchor_seq(gap_new_supplies, "new")
        ).items():
            if gap_old_supplies[a] in gap_ambiguous:
                continue
            matched[gap_olds[a]] = gap_news[b]

    relinked = tuple(
        Relinked(
            pair_id=olds[i].pair_id,
            tmp_ref=tmp_ref(job_id, olds[i].pair_id),
            final_ref=row_ref(job_id, news[j].row_index),
            final_row_index=news[j].row_index,
        )
        for i, j in sorted(matched.items())
    )
    orphaned = tuple(
        Orphaned(pair_id=p.pair_id, orphan_ref=orphan_ref(job_id, p.pair_id))
        for i, p in enumerate(olds)
        if i not in matched
    )
    return RelinkPlan(relinked=relinked, orphaned=orphaned)
