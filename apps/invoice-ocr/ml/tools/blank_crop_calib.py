"""빈 크롭 캘리브레이션 계층 — 측정 상태 어휘 · 육안 라벨 manifest · 마진 · 리포트 렌더.

`blank_crop_report`(fetch/report/apply CLI)에서 갈라져 나온 순수 계층이다. 여기에는 IO도
원격 접속도 없다 — 캐시 평가가 만든 record 리스트와 라벨 맵만 받아 임계 선정 근거를
계산하고 마크다운으로 렌더한다. 운영 DB에 쓰는 축(select_targets~build_apply_script)과는
공유하는 것이 측정 상태 어휘(crop_status)뿐이라 파일을 나눠도 결합이 늘지 않는다.
"""

import csv
from pathlib import Path

STATUS_OK = "ok"
STATUS_CROP_MISSING = "crop_missing"  # training_pairs 행은 있는데 crop PNG 없음
STATUS_CROP_UNREADABLE = "crop_unreadable"  # PNG는 있으나 cv2.imread가 None (손상·권한)
HOLD_STATUSES = (STATUS_CROP_MISSING, STATUS_CROP_UNREADABLE)

LABEL_BLANK = "blank"
LABEL_NONBLANK = "nonblank"
LABEL_VALUES = (LABEL_BLANK, LABEL_NONBLANK)

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
