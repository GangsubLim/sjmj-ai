"""빈 크롭 자동 배제 — 캘리브레이션 리포트 + 운영 DB 반영 도구 (Issue #38, ADR 0006).

배포 서버(macmini)의 training_pairs와 품목 크롭 PNG를 로컬 캐시로 동기화해, 크롭 잉크율
분포와 판정을 전수 산출하고(report) 확정된 임계로 운영 DB에 자동 배제를 반영한다(apply).
쓰기를 별 커맨드로 뗀 이유는 운영 DB 사고 방지다 — report를 눈으로 보고 apply를 친다.

계층 분리는 warp_gate_report와 같다: 순수 계층(파싱·마진·렌더·SQL 조립·결과 해석)은
단위테스트 대상이고, ssh/mysql 글루는 비대상이다.

Usage:
    uv run python -m tools.blank_crop_report fetch
    uv run python -m tools.blank_crop_report report --labels <labels.csv>
    uv run python -m tools.blank_crop_report apply [--recheck-reviewed] [--allow-holds]
"""

import csv
from dataclasses import dataclass
from pathlib import Path

from handwriting.blank_crop import (
    BLANK_CROP,
    STATUS_EXCLUDED,
    STATUS_INCLUDED,
    is_blank,
    is_machine_writable,
)

ML_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ML_ROOT / "results" / "blank_crop"

PAIRS_SQL = (
    "SELECT tp.id, tp.crop_ref, tp.job_id, tp.status, tp.exclusion_reason, j.curation_reviewed "
    "FROM training_pairs tp JOIN ocr_jobs j ON j.id = tp.job_id "
    "ORDER BY tp.job_id, tp.row_index"
)

STATUS_OK = "ok"
STATUS_CROP_MISSING = "crop_missing"  # training_pairs 행은 있는데 crop PNG 없음
STATUS_CROP_UNREADABLE = "crop_unreadable"  # PNG는 있으나 cv2.imread가 None (손상·권한)
HOLD_STATUSES = (STATUS_CROP_MISSING, STATUS_CROP_UNREADABLE)

LABEL_BLANK = "blank"
LABEL_NONBLANK = "nonblank"
LABEL_VALUES = (LABEL_BLANK, LABEL_NONBLANK)

# SQL 리터럴로 나갈 수 있는 값의 닫힌 집합 — 축별로 따로 닫는다(사유 값이 status 자리에
# 들어가도 SQL은 통과하지만 학습쌍 상태가 알 수 없는 값으로 뒤집히기 때문).
ALLOWED_PAIR_STATUS = (STATUS_INCLUDED, STATUS_EXCLUDED)
ALLOWED_EXCLUSION_REASON = (None, BLANK_CROP)


def _cell(value: str) -> str | None:
    return None if value == "NULL" else value


def parse_pairs_tsv(text: str) -> list[dict]:
    """mysql --batch TSV(training_pairs + 잡 검수 표식)를 dict 리스트로 파싱한다."""
    lines = text.strip().split("\n")
    header = lines[0].split("\t")
    out = []
    for ln in lines[1:]:
        if not ln.strip():
            continue
        d = dict(zip(header, ln.split("\t"), strict=True))
        out.append(
            {
                "id": int(d["id"]),
                "crop_ref": d["crop_ref"],
                "job_id": int(d["job_id"]),
                "pair_status": d["status"],
                "exclusion_reason": _cell(d["exclusion_reason"]),
                "curation_reviewed": d["curation_reviewed"] == "1",
            }
        )
    return out


def crop_path(cache: Path, crop_ref: str) -> Path:
    """crop_ref('job-42/row-0')에 대응하는 캐시 PNG 경로."""
    return cache / "crops" / f"{crop_ref}.png"


REQUIRED_LABEL_COLUMNS = ("crop_ref", "label")


def read_labels_csv(path: Path) -> list[dict]:
    """labels.csv를 dict 행 리스트로 읽는다(crop_ref, label, 확인자, 확인일).

    labels.csv는 사람이 Excel로 작성하는 파일이라 BOM·헤더 오타가 현실적이다.

    Raises:
        ValueError: 헤더에 crop_ref·label 컬럼이 없을 때. 없이 계속 읽으면 전 행의
            ref가 빈 문자열로 붕괴해 진단이 인코딩이 아닌 엉뚱한 곳(캐시 불일치 등)을
            가리킨다.
    """
    with path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        missing = [col for col in REQUIRED_LABEL_COLUMNS if col not in fieldnames]
        if missing:
            raise ValueError(
                f"labels.csv에 필수 컬럼이 없다({missing}) — 실제 헤더: {fieldnames}. "
                "Excel 저장 시 인코딩(BOM)이나 컬럼명을 확인할 것."
            )
        return list(reader)


def load_labels(rows: list[dict], known_refs: set[str]) -> dict[str, str]:
    """육안 라벨 manifest를 검증해 ref→label 맵으로 만든다.

    Raises:
        ValueError: 캐시에 없는 crop_ref · 중복 crop_ref · 알 수 없는 label 값.
            조용히 빠진 표본은 마진을 낙관적으로 만든다(spec §7).
    """
    labels: dict[str, str] = {}
    for row in rows:
        ref = (row.get("crop_ref") or "").strip()
        label = (row.get("label") or "").strip()
        if ref not in known_refs:
            raise ValueError(f"labels.csv의 crop_ref가 캐시에 없다: {ref} — fetch를 다시 실행할 것")
        if ref in labels:
            raise ValueError(f"labels.csv에 crop_ref가 중복이다: {ref}")
        if label not in LABEL_VALUES:
            raise ValueError(f"labels.csv의 label 값이 잘못됐다({ref}): {label!r} — {LABEL_VALUES}")
        labels[ref] = label
    return labels


def crop_status(*, exists: bool, readable: bool) -> str:
    """`crop_ink_ratio` 호출 전에 판정 불가를 가른다(spec §8, Task 2 리뷰 이월).

    `crop_ink_ratio(None)`은 `.size` 접근에서 `AttributeError`로 샌다 — 호출자(fetch/apply
    글루)는 cv2.imread 결과가 None인지 이 함수로 먼저 가른 뒤에만 `crop_ink_ratio`를
    부른다. 이 함수 자체는 cv2에 의존하지 않는 순수 분류다.

    Args:
        exists: crop_path가 가리키는 PNG가 캐시에 존재하는지.
        readable: exists=True일 때 cv2.imread가 성공했는지(None이 아닌지).
            exists=False면 이 값은 무의미하다.

    Returns:
        STATUS_CROP_MISSING · STATUS_CROP_UNREADABLE · STATUS_OK 중 하나.
    """
    if not exists:
        return STATUS_CROP_MISSING
    if not readable:
        return STATUS_CROP_UNREADABLE
    return STATUS_OK


def label_margin(records: list[dict], labels: dict[str, str]) -> dict | None:
    """라벨된 표본에서 정상최악 / 빈크롭최선 / 분리 마진(%)을 계산한다.

    · 정상최악 = nonblank 라벨 중 잉크율이 가장 **낮은** 값
    · 빈크롭최선 = blank 라벨 중 잉크율이 가장 **높은** 값
    임계는 이 둘 사이에 두고, 마진은 gap을 정상최악으로 나눈 비율로 적는다
    (warp_gate.py의 기존 임계 4종이 15.6~17.6% 마진).

    라벨된 표본이 crop_missing/crop_unreadable로 보류돼 측정에서 빠지면 어떤
    카운터에도 안 잡히고 조용히 사라진다 — 빠진 게 blank면 best_blank가 내려가
    마진이 낙관적으로 커진다(load_labels의 fail-fast가 막으려던 spec §7과 같은 문제가
    다른 문으로 들어온 것). `n_labeled_dropped`/`labeled_dropped_refs`로 드러낸다.

    Returns:
        한쪽 라벨군이 비면 None(마진 계산 불가). 아니면 아래 키를 담은 dict:
        worst_normal, best_blank, gap, margin_pct, denom_fallback(정상최악=0이라
        분모를 1.0으로 대체했는지), n_blank, n_nonblank, n_unlabeled(측정됐으나
        라벨 없음), n_labeled_dropped/labeled_dropped_refs(라벨은 있으나 보류라
        측정에서 빠진 건수·ref).
    """
    scored = [r for r in records if r["crop_status"] == STATUS_OK and r["ratio"] is not None]
    scored_refs = {r["crop_ref"] for r in scored}
    blank = [r["ratio"] for r in scored if labels.get(r["crop_ref"]) == LABEL_BLANK]
    nonblank = [r["ratio"] for r in scored if labels.get(r["crop_ref"]) == LABEL_NONBLANK]
    n_unlabeled = sum(1 for r in scored if r["crop_ref"] not in labels)
    dropped_refs = tuple(sorted(ref for ref in labels if ref not in scored_refs))
    if not blank or not nonblank:
        return None
    worst_normal, best_blank = min(nonblank), max(blank)
    gap = worst_normal - best_blank
    return {
        "worst_normal": worst_normal,
        "best_blank": best_blank,
        "gap": gap,
        "margin_pct": 100.0 * gap / (abs(worst_normal) or 1.0),
        # 정상최악이 0이면 비율의 기준이 없어 분모를 1.0으로 대체한다 — 그때 margin_pct는
        # 백분율이 아니라 gap의 절대값이다(warp_gate_report.py 관례와 동일).
        "denom_fallback": not abs(worst_normal),
        "n_blank": len(blank),
        "n_nonblank": len(nonblank),
        "n_unlabeled": n_unlabeled,
        "n_labeled_dropped": len(dropped_refs),
        "labeled_dropped_refs": dropped_refs,
    }


def count_exact_zero_ink(records: list[dict]) -> int:
    """잉크율이 정확히 0.0인 측정 건수를 별도 집계한다(spec §8, Task 2 리뷰 이월 #2).

    `_ink_mask`가 국소대비 기반이라 전면 클리핑·균일 크롭은 획이 있어도 잉크율이 0.0으로
    붕괴할 수 있다 — "잉크 0"과 "측정 불가"가 같은 값으로 붙는다는 뜻이다. 파일은 멀쩡해
    crop_unreadable 보류에도 걸리지 않는다. 여기서는 코드 동작을 바꾸지 않고 건수만
    드러낸다 — 유의미하면 이후 퇴화 검사를 추가할 근거가 된다.
    """
    return sum(1 for r in records if r["crop_status"] == STATUS_OK and r["ratio"] == 0.0)


def _render_margin_section(margin: dict | None, labels: dict[str, str]) -> list[str]:
    if margin is None:
        # 라벨 0건과 "한쪽뿐"은 원인이 다르다 — 같은 문구로 붕괴시키면 --labels를 안 준
        # 것을 "반대쪽만 더 라벨하면 된다"로 오독하게 된다.
        if not labels:
            return [
                "## 임계 선정 근거",
                "",
                "- 라벨된 표본이 0건이다 — --labels로 labels.csv를 지정할 것.",
            ]
        return [
            "## 임계 선정 근거",
            "",
            "- 라벨된 표본이 한쪽(blank 또는 nonblank)뿐이라 마진을 계산할 수 없다 — "
            "labels.csv를 보강할 것.",
        ]
    lines = [
        "## 임계 선정 근거",
        "",
        f"- 정상최악(nonblank 최저 잉크율): {margin['worst_normal']:.5f} (n={margin['n_nonblank']})",
        f"- 빈크롭최선(blank 최고 잉크율): {margin['best_blank']:.5f} (n={margin['n_blank']})",
        f"- gap {margin['gap']:.5f} · 마진 {margin['margin_pct']:.1f}%"
        f"{'*' if margin['denom_fallback'] else ''}",
        f"- 라벨 없는 표본 {margin['n_unlabeled']}건(분포에만 실리고 근거로는 쓰지 않는다)",
    ]
    if margin["n_labeled_dropped"]:
        lines.append(
            f"- ⚠️ 라벨된 표본 {margin['n_labeled_dropped']}건이 측정 보류라 근거에서 "
            f"빠졌다: {', '.join(margin['labeled_dropped_refs'])}"
        )
    if margin["denom_fallback"]:
        lines += [
            "",
            "\\*: 정상최악값이 0이라 마진% 분모를 1.0으로 대체했다 — "
            "백분율이 아니라 gap의 절대값이다.",
        ]
    if margin["gap"] <= 0:
        lines += [
            "",
            "> ⚠️ 두 분포가 겹친다(gap ≤ 0) — 오탐 0을 우선해 보수적으로 잡는다. 배제된 "
            "정상 쌍은 사람이 되돌리지 않으면 그대로 사라지지만, 오염은 큐레이션에서 "
            "잡을 기회가 한 번 더 있다.",
        ]
    return lines


def render_blank_report(
    records: list[dict], labels: dict[str, str], meta: dict, threshold: float | None = None
) -> str:
    """잉크율 분포·판정·보류를 마크다운으로 렌더한다.

    Args:
        threshold: 확정된(또는 미확정인) `BLANK_INK_MAX`. 모듈 전역을 이 함수가 직접
            읽지 않고 호출자(Task 10/11 CLI가 `handwriting.blank_crop`에서 읽어 넘김)가
            주입한다 — 같은 인자로 호출하면 임계 확정 전후로 출력이 갈리지 않는다.
    """
    scored = [r for r in records if r["crop_status"] == STATUS_OK and r["ratio"] is not None]
    holds = [r for r in records if r["crop_status"] in HOLD_STATUSES]
    threshold_line = (
        f"- 임계 BLANK_INK_MAX = {threshold:.5f}"
        if threshold is not None
        else "- 임계 BLANK_INK_MAX: 미확정"
    )
    lines = [
        "# 빈 크롭 캘리브레이션 리포트",
        "",
        f"- 동기화: {meta.get('fetched_at', '?')} · 호스트 {meta.get('host', '?')}",
        f"- 학습쌍 {len(records)} = 측정 {len(scored)} + 보류 {len(holds)}",
        threshold_line,
        f"- 잉크율 정확히 0.0인 표본 {count_exact_zero_ink(records)}건(참고 — 균일/클리핑 크롭에서 측정 자체가 붕괴했을 수 있다)",
        "",
        *_render_margin_section(label_margin(records, labels), labels),
        "",
        "## 잉크율 전수 (오름차순)",
        "",
        "| crop_ref | 잉크율 | 육안 라벨 | pair_status(DB) | 사유 | 검수완료 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in sorted(scored, key=lambda x: x["ratio"]):
        lines.append(
            f"| {r['crop_ref']} | {r['ratio']:.5f} | {labels.get(r['crop_ref'], '—')} | "
            f"{r['pair_status']} | {r['exclusion_reason'] or '—'} | "
            f"{'Y' if r['curation_reviewed'] else '—'} |"
        )
    lines += ["", "## 보류 (판정 불가 — DB 미변경)", ""]
    lines += [f"- {r['crop_ref']}: {r['crop_status']}" for r in holds] or ["- 없음"]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class PairUpdate:
    """조건부 UPDATE 1건 — 캐시에서 본 상태(seen)와 목표 상태(new)를 함께 싣는다."""

    pair_id: int
    job_id: int
    seen_status: str
    seen_reason: str | None
    new_status: str
    new_reason: str | None


def select_targets(records: list[dict], *, recheck_reviewed: bool) -> list[dict]:
    """잡 단위 가드 — 검수 완료된 잡은 기계가 손대지 않는다(--recheck-reviewed로 해제)."""
    if recheck_reviewed:
        return list(records)
    return [r for r in records if not r["curation_reviewed"]]


def plan_updates(records: list[dict], threshold: float) -> tuple[list[PairUpdate], dict[str, int]]:
    """판정을 §6 불변식에 통과시켜 조건부 UPDATE 계획으로 바꾼다.

    측정 축(`crop_status`)과 DB 축(`pair_status`)은 다른 축이다 — 보류 게이트는 전자로,
    조건부 UPDATE의 WHERE에 실리는 seen 상태는 후자로 판단한다.

    보류(잉크율 없음)는 계획에서 빠지고 DB에서 기존 상태 그대로 남는다 — '보류'라는
    세 번째 상태값은 존재하지 않는다(spec §8). 목표 상태가 캐시에서 본 상태와 같은 쌍에는
    UPDATE를 쏘지 않는다: MySQL의 affected row는 실제로 바뀐 행 수라 no-op도 0을 내므로,
    안 쏘는 쌍을 걸러야 affected 0이 언제나 충돌을 뜻하게 된다.

    Args:
        records: `select_targets`를 통과한 집합이어야 한다(M3) — 잡 단위 검수완료 가드는
            여기서 다시 걸지 않는다. 순서를 빠뜨리면 검수 완료 잡을 기계가 조용히 쓴다.
            그 외엔 fetch/측정이 끝난 레코드(`crop_status`·`ratio`·`pair_status`·
            `exclusion_reason`·`id`·`job_id`).
        threshold: 확정된 `BLANK_INK_MAX`.

    Returns:
        (계획, {"protected": 사람 판정이라 건드리지 않은 수, "unchanged": 이미 목표 상태인 수})
    """
    updates: list[PairUpdate] = []
    counts: dict[str, int] = {"protected": 0, "unchanged": 0}
    for r in records:
        if r["crop_status"] != STATUS_OK or r["ratio"] is None:
            continue
        seen_status, seen_reason = r["pair_status"], r["exclusion_reason"]
        if not is_machine_writable(seen_status, seen_reason):
            counts["protected"] += 1
            continue
        if is_blank(r["ratio"], threshold):
            new_status, new_reason = STATUS_EXCLUDED, BLANK_CROP
        else:
            # 기계가 자기 판정을 취소할 때 사유를 반드시 NULL로 지운다 — 남기면
            # '사람이 되돌림' 칸과 구분되지 않아 자기 정정 결과를 영구 보호해버린다.
            new_status, new_reason = STATUS_INCLUDED, None
        if (new_status, new_reason) == (seen_status, seen_reason):
            counts["unchanged"] += 1
            continue
        updates.append(
            PairUpdate(
                pair_id=r["id"],
                job_id=r["job_id"],
                seen_status=seen_status,
                seen_reason=seen_reason,
                new_status=new_status,
                new_reason=new_reason,
            )
        )
    return updates, counts


def _status_lit(value: str) -> str:
    """pair status를 SQL 리터럴로 만든다(닫힌 집합 밖이면 즉시 실패 — 주입 표면 없음)."""
    if value not in ALLOWED_PAIR_STATUS:
        raise ValueError(f"허용되지 않은 status 리터럴: {value!r} — {ALLOWED_PAIR_STATUS}")
    return f"'{value}'"


def _reason_lit(value: str | None) -> str:
    """exclusion_reason을 SQL 리터럴로 만든다(NULL 포함 닫힌 집합)."""
    if value not in ALLOWED_EXCLUSION_REASON:
        raise ValueError(f"허용되지 않은 사유 리터럴: {value!r} — {ALLOWED_EXCLUSION_REASON}")
    return "NULL" if value is None else f"'{value}'"


def _id_lit(value: int) -> str:
    """pair_id/job_id를 SQL 리터럴로 만든다(H1 — 정수 축도 문자열 축과 같이 닫는다).

    `PairUpdate`의 `int` 타입힌트는 런타임에 강제되지 않는다 — 현재 유일한 생산 경로가
    `parse_pairs_tsv`의 `int()`라 도달 불가일 뿐, 검증을 호출자 기억에만 맡기면 방어가
    아니다. `bool`은 `int`의 서브클래스라 `isinstance(value, int)`만으로는 걸러지지
    않으므로 별도로 배제한다.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"허용되지 않은 id 리터럴: {value!r} — int만 허용")
    return str(value)


def build_apply_script(updates: list[PairUpdate]) -> str:
    """조건부 UPDATE + ROW_COUNT 프로브 + 잡 검수표식 되돌림을 한 트랜잭션으로 조립한다.

    각 UPDATE는 캐시에서 본 상태를 WHERE에 실어 fetch~apply 사이의 사람 PATCH를 덮지 않는다
    (파이썬 술어는 SELECT한 행에만 적용되므로 WHERE에 재표명하지 않으면 그 사이에 들어온
    사람 PATCH를 덮는다). exclusion_reason 비교는 항상 `<=>`다 — `= NULL`은 항상 거짓이라
    사유가 NULL인 쌍이 전부 조용히 0행이 된다.

    잡 검수표식은 **미처리 쌍이 남은 잡만** 되돌린다(EXISTS로 SQL 안에서 유도) — spec §4③의
    "판정이 바뀐 쌍을 가진 잡"을 근사 없이 충족한다. 조건부 UPDATE가 실제로 성공한 쌍에만
    reviewed_at = NULL을 쓰므로, 뒤 시점의 '미처리 쌍이 있는 잡'은 곧 '판정이 바뀐 쌍이
    있는 잡'이다. 충돌만 있고 변경 0인 잡은 조건이 거짓이 되어 표식이 유지되므로
    '미검수 + 미처리 0'(사람이 "볼 것 없음"으로 읽는 상태)이 만들어지지 않는다.

    ROW_COUNT 프로브는 전부 COMMIT 앞에서 출력된다(M4) — 교착·연결 끊김으로 COMMIT 자체가
    실패해도 stdout엔 이미 `affected 1`이 찍혀 있어, 출력만 보면 "적용 성공"처럼 읽힐 수
    있다. 원자성 자체는 문제없다(부분 적용 없음). **Task 11이 지켜야 할 계약**: apply 글루는
    반드시 `tools/remote.py`의 `run_ssh`를 경유해 mysql 비-0 종료를 `RemoteError`로 올려야
    하고, stdout 파싱 결과(`parse_apply_output`/`classify_affected`)만으로 성공 판정하지
    말 것. sentinel(예: `SELECT 'committed'`)을 추가하면 이 문제를 없앨 수 있지만 파서와
    동시 수정이 필요해 커플링이 생기므로 여기서는 하지 않는다.

    Returns:
        실행 가능한 SQL 스크립트. 계획이 비면 빈 문자열(쏠 것이 없다).

    Raises:
        ValueError: status·사유가 닫힌 집합 밖이거나, pair_id/job_id가 int가 아닐 때(H1).
    """
    if not updates:
        return ""
    parts = ["START TRANSACTION;"]
    for u in updates:
        pair_id_lit = _id_lit(u.pair_id)
        parts.append(
            "UPDATE training_pairs SET "
            f"status = {_status_lit(u.new_status)}, "
            f"exclusion_reason = {_reason_lit(u.new_reason)}, "
            "reviewed_at = NULL "
            f"WHERE id = {pair_id_lit} AND status = {_status_lit(u.seen_status)} "
            f"AND exclusion_reason <=> {_reason_lit(u.seen_reason)};"
        )
        parts.append(f"SELECT {pair_id_lit} AS pair_id, ROW_COUNT() AS affected;")
    job_id_set = {u.job_id for u in updates}
    for jid in job_id_set:
        _id_lit(jid)  # sorted()가 mixed-type 비교로 TypeError를 내기 전에 먼저 검증한다
    job_ids = ", ".join(str(j) for j in sorted(job_id_set))
    parts.append(
        "UPDATE ocr_jobs SET curation_reviewed = FALSE "
        f"WHERE id IN ({job_ids}) AND EXISTS ("
        "SELECT 1 FROM training_pairs tp "
        "WHERE tp.job_id = ocr_jobs.id AND tp.reviewed_at IS NULL);"
    )
    parts.append("COMMIT;")
    return "\n".join(parts) + "\n"


def parse_apply_output(text: str) -> dict[int, int]:
    """ROW_COUNT 프로브 출력(TSV, 결과셋마다 헤더 반복)을 pair_id→affected로 파싱한다.

    엄격 파싱을 택했다(M1) — 선례 `tools/bank_update.py`의 `parse_reviewed_job_ids`는
    관용 파싱(`isdigit`으로 조용히 skip)이지만, 거긴 단순 조회고 여긴 실제 운영 DB에
    반영된 UPDATE의 결과 확인이라 조용한 skip이 곧 거짓 안심("변경 성공")으로 직결된다.

    Raises:
        ValueError: 프로브 형식(`숫자\\t숫자`)이 아닌 줄을 만났을 때(경고 줄 혼입,
            `--batch` 누락 등 — 원문 줄을 메시지에 실어 원인을 짚게 한다) · 같은
            pair_id가 두 번 이상 나올 때(M2 — stale/잘린 출력 혼입을 조용히 덮지 않는다).
    """
    out: dict[int, int] = {}
    for raw in text.strip().split("\n"):
        line = raw.strip()
        if not line or line.startswith("pair_id"):
            continue
        pid, _, affected = line.partition("\t")
        try:
            parsed_pid, parsed_affected = int(pid), int(affected)
        except ValueError as e:
            raise ValueError(f"apply 출력 해석 실패 — 프로브 형식이 아닌 줄: {raw!r}") from e
        if parsed_pid in out:
            raise ValueError(
                f"apply 출력에 pair_id 중복: {parsed_pid} — stale 출력이 섞였을 수 있다"
            )
        out[parsed_pid] = parsed_affected
    return out


def classify_affected(updates: list[PairUpdate], affected: dict[int, int]) -> dict[str, list[int]]:
    """affected row를 변경/충돌/미지로 가른다 — 안 쏜 쌍을 걸렀으므로 0은 언제나 충돌이다.

    프로브 자체가 없는 쌍(스크립트 중단 등)도 적용됐다고 볼 근거가 없어 충돌로 센다.

    unknown(M2): 계획(updates)에 없는 pair_id가 프로브에 나타난 경우다 — 지금까진 조용히
    버려져 stale/잘린 출력이 섞여도 "충돌 0"이라는 거짓 안심이 나왔다.
    """
    planned_ids = {u.pair_id for u in updates}
    changed = [u.pair_id for u in updates if affected.get(u.pair_id, 0) > 0]
    conflict = [u.pair_id for u in updates if affected.get(u.pair_id, 0) <= 0]
    unknown = sorted(pid for pid in affected if pid not in planned_ids)
    return {"changed": changed, "conflict": conflict, "unknown": unknown}


def apply_exit_code(
    holds: list[dict],
    conflicts: list[int],
    *,
    allow_holds: bool,
    unknown: tuple[int, ...] = (),
) -> int:
    """보류 또는 충돌(계획 밖 id 포함)이 있으면 비-0으로 끝난다.

    런북에서 apply는 bank_update의 앞 단계이므로(spec §5), 여기서 성공으로 끝나면 운영자가
    부분 적용 상태로 뱅크 갱신에 넘어간다. 보류는 --allow-holds로 의도적 무시가 가능하지만
    충돌·unknown에는 우회 플래그를 두지 않는다(M2) — 정확한 해소는 재-fetch 후 재실행이고,
    도구가 멱등이라 재실행 비용이 낮다(spec §8).
    """
    if conflicts or unknown:
        return 1
    if holds and not allow_holds:
        return 1
    return 0
