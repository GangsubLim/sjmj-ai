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
    """재처리 이전의 확정 학습쌍(training_pairs 1행에서 필요한 것만)."""

    pair_id: int
    row_index: int
    supply: int | None


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


def plan_relink(job_id: int, old_pairs: list[OldPair], new_rows: list[NewRow]) -> RelinkPlan:
    """옛 확정 쌍을 새 검출 행에 짝지어 승계·미결 계획을 만든다.

    행검출의 변화는 순서 보존 편집이다 — 줄이 추가·삭제될 뿐 물리적 순서가 섞이지 않는다.
    따라서 매칭은 최장 공통 부분수열이며 stdlib difflib으로 코어 규약을 지킨 채 구현한다.
    ocr_poc/score.py:align_rows는 선례지만 greedy first-match에 순서 제약이 없어 승계용으로
    쓰지 않는다(틀리면 오염된 학습쌍이 뱅크에 들어간다).

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

    # autojunk=False — 기본 휴리스틱은 원소가 200개를 넘을 때 흔한 값을 junk로 빼는데,
    # 여기서 흔한 값(반복 금액)은 버릴 대상이 아니라 그룹 게이트가 판정할 대상이다.
    matcher = SequenceMatcher(
        a=_anchor_seq(old_supplies, "old"), b=_anchor_seq(new_supplies, "new"), autojunk=False
    )
    matched: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            i = block.a + k
            if olds[i].supply in ambiguous:
                continue  # 정렬이 짝을 지었어도 개수가 어긋난 값은 전량 미결로 민다
            matched[i] = block.b + k

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
