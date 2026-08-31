"""warp 게이트 재워프 리포트의 순수 계층 — 라벨 3분할 · 축별 마진 · 저장워프 대조 · 렌더.

`warp_gate_report`(fetch/report CLI, cv2 글루)에서 갈라져 나온 순수 계층이다. IO도 원격
접속도 cv2도 없다 — evaluate_rewarped가 만든 record 리스트만 받아 Phase 1 임계 도출의
근거(축별 정상군 최악값 vs 파손군 최선값)를 계산하고 마크다운으로 렌더한다.

⚠️ 레이어 방향: `warp_gate_report`(CLI) → 이 모듈(순수) 단방향이다. 이 모듈은
`tools.warp_gate_report`를 import하지 않는다(레포의 기존 3층 도구 —
`blank_crop_report`→`blank_crop_calib`, `curation_report`→`curation_enrich`/`render`/`cohort`
— 와 동일 관례). 역방향으로 걸면 module-level 순환 import로
`ImportError: cannot import name ... from partially initialized module`가 난다.

지표 어휘(METRIC_KEYS/HIGHER_IS_BETTER/DERIVED_METRICS/metric_value)도 여기서 소유한다 —
저장 워프 참고 축(stored_vs_rewarp)과 재워프 주 기준(evaluate_rewarped) 양쪽이 같은 어휘를 쓴다.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import fields

from handwriting.warp_gate import (
    WarpGateMetrics,
    WarpGateMetricsEnh,
    blue_asymmetry,
    evaluate_warp,
    evaluate_warp_enh,
)

# 원시 지표는 DTO 필드에서 파생한다 — WarpGateMetrics에 필드가 늘면 표가 자동으로 따라간다.
RAW_METRIC_KEYS = tuple(f.name for f in fields(WarpGateMetrics))
# 게이트 술어가 실제로 검사하는 파생 지표 2종. evaluate_warp는 원시 L·R 각각이 아니라
# min(L,R)과 좌우 비대칭도를 보므로, 이 둘이 없으면 ④ 규칙의 마진 행이 아예 비고 ③ 규칙은
# 서로 다른 잡의 L·R 극값이 한 행에 섞여 분리 마진을 실제보다 낙관적으로 보이게 한다.
DERIVED_METRICS: dict[str, Callable[[dict], float]] = {
    "blue_ratio_min": lambda m: min(m["blue_ratio_left"], m["blue_ratio_right"]),
    "blue_asym": lambda m: blue_asymmetry(m["blue_ratio_left"], m["blue_ratio_right"]),
}
METRIC_KEYS = RAW_METRIC_KEYS + tuple(DERIVED_METRICS)
# 값이 클수록 좋은 지표 — 나머지는 작을수록 좋다. 마진 계산 방향을 정한다.
HIGHER_IS_BETTER = frozenset(
    {"hline_count", "blue_ratio_left", "blue_ratio_right", "blue_ratio_min"}
)

LABEL_NORMAL = "normal"
LABEL_SUSPECT = "suspect"
LABEL_UNLABELED = "unlabeled"


def metric_value(metrics: dict, key: str) -> float:
    """지표 dict에서 표에 쓸 값을 뽑는다(파생 지표는 게이트 술어와 동일한 식으로 계산)."""
    derive = DERIVED_METRICS.get(key)
    return derive(metrics) if derive else metrics[key]


def label_of(job_id: int, suspects: set[int], unlabeled: set[int]) -> str:
    """잡의 라벨을 정한다 — 파손 판정이 미라벨보다 우선한다(육안 편입, spec §7)."""
    if job_id in suspects:
        return LABEL_SUSPECT
    if job_id in unlabeled:
        return LABEL_UNLABELED
    return LABEL_NORMAL


def axis_margins(records: list[dict], axis: str) -> dict:
    """마스크 축(std|enh)별 정상군 최악값 vs 파손군 최선값과 분리 마진(%)을 계산한다.

    계약: usable 레코드의 metrics는 "std"·"enh" 두 축을 항상 함께 갖는다(job_metrics가
    두 축을 한 번에 만든다). 한쪽만 있는 레코드는 상류 배선 버그이므로 KeyError로
    fail-fast한다 — `.get()`으로 흡수하면 그 레코드가 조용히 마진 계산에서 빠져
    worst_normal이 왜곡된다(parse_job_rows_tsv가 같은 이유로 fail-fast하는 것과 동일).

    unlabeled 라벨은 정상군·파손군 어디에도 넣지 않는다 — 섞이면 정상군 '최악값'이
    미확인 잡에서 나와 임계 근거가 오염된다(spec §7).

    산술 자체는 `_margins`가 소유한다(M1) — 저장 워프/재워프 두 축이 군 분리 방식만 달리해
    이 함수를 통해 같은 산술을 공유한다.
    """
    usable = [r for r in records if r["metrics"]]
    normal = [r["metrics"][axis] for r in usable if r["label"] == LABEL_NORMAL]
    suspect = [r["metrics"][axis] for r in usable if r["label"] == LABEL_SUSPECT]
    return _margins(normal, suspect)


def _margins(normal: list[dict], suspect: list[dict]) -> dict:
    """정상군·파손군 지표 dict 리스트에서 지표별 분리 마진(worst_normal/best_suspect/gap/%)을 낸다.

    지표별 방향(HIGHER_IS_BETTER)에 따라 '정상군 최악값'과 '파손군 최선값'을 고르고 그
    gap을 항상 "정상 쪽이 유리한 방향"으로 계산한다 — 방향을 반전하면(부호 뒤집힘) 표의
    마진이 실제와 반대로 읽힌다.
    """
    out = {}
    for k in METRIC_KEYS:
        if not normal or not suspect:
            out[k] = None
            continue
        nv = [metric_value(m, k) for m in normal]
        sv = [metric_value(m, k) for m in suspect]
        higher = k in HIGHER_IS_BETTER
        worst_normal, best_suspect = (min(nv), max(sv)) if higher else (max(nv), min(sv))
        gap = worst_normal - best_suspect if higher else best_suspect - worst_normal
        out[k] = {
            "worst_normal": worst_normal,
            "best_suspect": best_suspect,
            "gap": gap,
            "margin_pct": 100.0 * gap / (abs(worst_normal) or 1.0),
            # 정상군 최악값이 0이면 비율의 기준이 없어 분모를 1.0으로 대체한다 — 그때
            # margin_pct는 백분율이 아니라 gap의 절대값이므로 렌더가 각주를 달아야 한다.
            "denom_fallback": not abs(worst_normal),
        }
    return out


# 저장 워프 대조표(stored_vs_rewarp)의 float 재계산 허용오차 — 1e-12 수준의 부동소수 노이즈를
# drift로 오인하면 육안 대상이 과대 편입된다(L1). 정수 지표(hline_count)는 이 상수를 타지
# 않고 여전히 완전일치로 비교한다.
_STORED_DRIFT_TOLERANCE = 1e-9


def _metric_drifted(rewarp_value: float, stored_value: float) -> bool:
    """저장워프 대조 값이 실질적으로 다른지 판정한다. float은 허용오차, 그 외는 완전일치."""
    if isinstance(rewarp_value, float) or isinstance(stored_value, float):
        return abs(rewarp_value - stored_value) > _STORED_DRIFT_TOLERANCE
    return rewarp_value != stored_value


def stored_vs_rewarp(records: list[dict]) -> list[dict]:
    """저장 warped.png 기준 std 지표와 재워프 기준이 어긋나는 잡만 뽑는다(워프 경로 드리프트).

    어긋나는 잡은 #18 승계 라벨이 무효라 Phase 1 육안 대상에 추가돼야 한다(spec §4.1).
    """
    rows = []
    for r in records:
        stored, metrics = r.get("stored_metrics"), r.get("metrics")
        if not stored or not metrics:
            continue
        diff = {
            k: {"rewarp": metrics["std"][k], "stored": stored[k]}
            for k in stored
            if _metric_drifted(metrics["std"][k], stored[k])
        }
        if diff:
            rows.append({"job_id": r["job_id"], **diff})
    return rows


# ---------------------------------------------------------------------------
# 판정 전이 4분류 (Task 12) — M5: 리포트가 운영 술어를 직접 호출해 근거를 재현 가능하게 한다.
# ---------------------------------------------------------------------------

FLIP_PASS_PASS = "pass→pass"
FLIP_FAIL_PASS = "fail→pass"
FLIP_FAIL_FAIL = "fail→fail"
FLIP_PASS_FAIL = "pass→fail"  # 설계상 발생 불가 — 표가 비어 있음을 증거로 남기기 위해 존재한다

RESCUE_JOBS = frozenset({59, 60, 61, 62, 63, 65, 66, 67})  # 오강등 구제 전량(DoD 2, spec §4.2)
DAMAGED_JOBS = frozenset({17, 21, 24, 29, 30, 31, 39})  # 파손 확정 전량(DoD 3, spec §4.2)


def classify_flip(metrics: dict) -> str:
    """표준 판정 → enh 폴백 판정의 전이를 4분류한다(spec §4.2).

    운영 술어(evaluate_warp/evaluate_warp_enh)를 직접 호출한다 — 판정 로직을 리포트 쪽에
    복제하지 않는다(#60 리뷰 M5). 임계 상수가 바뀌면 이 분류도 그대로 따라 움직여 근거가
    코드로 재현 가능하게 유지된다.

    "표준 먼저 → 실패 시에만 enh"라는 **분기 순서** 자체는 여전히 이 함수와
    `handwriting.infer_job._warp_gate_passes` 양쪽이 각자 소유한다(cv2 무의존 규약 때문에
    그 함수를 직접 재사용할 수 없다) — 운영 쪽 분기 순서가 바뀌면 이 함수도 함께 고쳐야
    한다. 그러지 않으면 이 함수는 옛 구조로 계속 `pass→fail: []`을 출력해 거짓 증거를
    낸다(#60 리뷰 M2).

    metrics는 std·enh 두 축을 항상 함께 가져야 한다(job_metrics 계약). std가 통과해 enh를
    참조하지 않는 정상 경로에서도 이 계약을 미리 검사한다 — 그러지 않으면 enh 키가
    통째로 빠진 상류 배선 버그가 std 통과 시엔 조용히 숨었다가 std 실패 시에만
    KeyError로 드러나는 비대칭이 생긴다(#60 리뷰 L1).

    Raises:
        KeyError: `metrics`에 "std" 또는 "enh" 축이 없을 때.
    """
    missing = {"std", "enh"} - metrics.keys()
    if missing:
        raise KeyError(f"classify_flip: metrics에 {sorted(missing)} 축이 없다")
    if evaluate_warp(WarpGateMetrics(**metrics["std"])):
        return FLIP_PASS_PASS
    if evaluate_warp_enh(WarpGateMetricsEnh(**metrics["enh"])):
        return FLIP_FAIL_PASS
    return FLIP_FAIL_FAIL


def flip_table(records: list[dict]) -> dict[str, list[int]]:
    """잡을 판정 전이별로 묶는다. `pass→fail` 키는 항상 존재하며 구성상 비어 있어야 한다.

    `axis_margins`와 동일한 이유로 `r["metrics"]`를 fail-fast로 읽는다(#60 리뷰 M5) —
    `.get()`으로 흡수하면 metrics 키 자체가 없는 상류 배선 버그가 조용히 분모에서
    빠진다. `metrics`가 `None`인 레코드(§4.1 분모 제외 대상)는 계약대로 키는 있으니
    그대로 스킵된다.
    """
    out: dict[str, list[int]] = {
        FLIP_PASS_PASS: [],
        FLIP_FAIL_PASS: [],
        FLIP_FAIL_FAIL: [],
        FLIP_PASS_FAIL: [],
    }
    for r in records:
        if r["metrics"]:
            out[classify_flip(r["metrics"])].append(r["job_id"])
    return out


def _dod_marks(table: dict[str, list[int]]) -> dict[str, bool]:
    """flip 표에서 DoD 1~3(spec §4.2)을 자동판정한다.

    DoD 1은 `classify_flip`이 표준 통과 시 즉시 반환해 `pass→fail`을 구성상 방출할 수 없으므로
    이 표가 비어 있음 자체가 증거다(운영 배선 테스트가 그 전제를 별도로 고정한다).

    DoD 2·3의 `>=`(초과집합)는 잡이 더 있어도 무방하다는 의도지만, 기준 집합
    (RESCUE_JOBS/DAMAGED_JOBS)이 비면 `set() >= frozenset()`이 항상 참이라 모집단 없이도
    무조건 ✅가 나온다 — 이 침묵 붕괴를 막기 위해 기준 집합이 비어 있지 않은지 먼저
    본다(#60 리뷰 M3).
    """
    return {
        "dod1_no_pass_to_fail": not table[FLIP_PASS_FAIL],
        "dod2_rescue_all_fail_to_pass": bool(RESCUE_JOBS)
        and set(table[FLIP_FAIL_PASS]) >= RESCUE_JOBS,
        "dod3_damaged_all_fail_to_fail": bool(DAMAGED_JOBS)
        and set(table[FLIP_FAIL_FAIL]) >= DAMAGED_JOBS,
    }


def _render_flip_section(table: dict[str, list[int]], total_records: int) -> list[str]:
    """판정 전이 4분류 절을 렌더한다(spec §4.2) — 전이별 잡 id + DoD 1~3 자동판정.

    Args:
        table: `flip_table`의 출력.
        total_records: 재워프 리포트 전체 모집단 크기(`len(records)`). metrics=None인 잡은
            `flip_table`이 조용히 건너뛰므로(§4.1 분모 제외), 절 머리에 합계/모집단/제외
            건수를 나란히 적어 눈에 띄게 한다(#60 리뷰 M4).
    """
    marks = _dod_marks(table)
    classified = sum(len(v) for v in table.values())
    return [
        "## 판정 전이 4분류",
        "",
        f"- 합계 {classified} / 모집단 {total_records} (지표 없음 {total_records - classified})",
        f"- {FLIP_PASS_PASS}: {sorted(table[FLIP_PASS_PASS])}",
        f"- {FLIP_FAIL_PASS}: {sorted(table[FLIP_FAIL_PASS])}",
        f"- {FLIP_FAIL_FAIL}: {sorted(table[FLIP_FAIL_FAIL])}",
        f"- {FLIP_PASS_FAIL}: {sorted(table[FLIP_PASS_FAIL])}",
        "",
        f"- DoD 1(`pass→fail` 0건): {'✅' if marks['dod1_no_pass_to_fail'] else '❌'} "
        "(구성상 보장 — classify_flip이 표준 통과 시 즉시 반환)",
        f"- DoD 2(오강등 구제 {sorted(RESCUE_JOBS)} 전부 fail→pass): "
        f"{'✅' if marks['dod2_rescue_all_fail_to_pass'] else '❌'}",
        f"- DoD 3(파손 확정 {sorted(DAMAGED_JOBS)} 전부 fail→fail): "
        f"{'✅' if marks['dod3_damaged_all_fail_to_fail'] else '❌'}",
    ]


def _render_axis_margin_table(title: str, margins: dict) -> list[str]:
    """마스크 축 하나의 분리 마진 표를 렌더한다(axis_margins의 출력 shape 전용)."""
    rows = []
    for k, v in margins.items():
        if not v:
            rows.append(f"| {k} | — | — | — | (정상군 또는 파손군이 비어 계산 불가) |")
            continue
        rows.append(
            f"| {k} | {v['worst_normal']:.4f} | {v['best_suspect']:.4f} | "
            f"{v['gap']:.4f} | {v['margin_pct']:.1f}%{'*' if v['denom_fallback'] else ''} |"
        )
    lines = [
        f"## {title} 마스크 기준 분리 마진",
        "",
        "| 지표 | 정상군 최악값 | 파손군 최선값 | gap | 마진% |",
        "| --- | --- | --- | --- | --- |",
        *rows,
    ]
    if any(v and v["denom_fallback"] for v in margins.values()):
        lines += [
            "",
            "\\*: 정상군 최악값이 0이라 마진% 분모를 1.0으로 대체했다 — "
            "백분율이 아니라 gap의 절대값이다.",
        ]
    return lines


def render_rewarp_report(records: list[dict], margins: dict, drift: list[dict], meta: dict) -> str:
    """재워프 기준 게이트 리포트를 마크다운으로 렌더한다.

    Args:
        records: `evaluate_rewarped`의 출력.
        margins: `{"std": axis_margins(records, "std"), "enh": axis_margins(records, "enh")}`.
        drift: `stored_vs_rewarp(records)`.
        meta: fetch가 남긴 캐시 메타(fetched_at·host).
    """
    status_counts = Counter(r["status"] for r in records)
    label_counts = Counter(r["label"] for r in records)
    flip = flip_table(records)
    lines = [
        "# warp 정합 게이트 — 재워프 기준 캘리브레이션 리포트",
        "",
        f"- 동기화: {meta.get('fetched_at', '?')} · 호스트 {meta.get('host', '?')}",
        f"- 전체 잡 {len(records)} = "
        + " + ".join(f"{k} {v}" for k, v in sorted(status_counts.items())),
        f"- 라벨 분포 — normal {label_counts.get(LABEL_NORMAL, 0)} · "
        f"suspect {label_counts.get(LABEL_SUSPECT, 0)} · "
        f"unlabeled {label_counts.get(LABEL_UNLABELED, 0)}",
        "",
        *_render_axis_margin_table("표준", margins.get("std") or {}),
        "",
        *_render_axis_margin_table("enh", margins.get("enh") or {}),
        "",
        *_render_flip_section(flip, len(records)),
        "",
        "## 지표 전수표",
        "",
        "| job | 라벨 | std hline | std pitch | std L | std R | "
        "enh hline | enh pitch | enh L | enh R | 이전 warp_ok |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in records:
        m = r.get("metrics")
        if not m:
            continue
        s, e = m["std"], m["enh"]
        lines.append(
            f"| {r['job_id']} | {r['label']} | {s['hline_count']} | {s['pitch_dev']:.3f} | "
            f"{s['blue_ratio_left']:.4f} | {s['blue_ratio_right']:.4f} | "
            f"{e['hline_count']} | {e['pitch_dev']:.3f} | "
            f"{e['blue_ratio_left']:.4f} | {e['blue_ratio_right']:.4f} | {r['prev_warp_ok']} |"
        )
    lines += ["", "## 저장 워프 대조표", ""]
    if drift:
        lines += ["| job | 지표 | 재워프 | 저장 |", "| --- | --- | --- | --- |"]
        for d in drift:
            lines += [
                f"| {d['job_id']} | {k} | {v['rewarp']} | {v['stored']} |"
                for k, v in d.items()
                if k != "job_id"
            ]
    else:
        lines.append("차이 없음")
    lines += ["", "## 분모 제외 (게이트 무관)", ""]
    excluded = [r for r in records if not r.get("metrics")]
    if excluded:
        lines += [f"- job {r['job_id']}: {r['status']}" for r in excluded]
    else:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# crop-identity 스냅샷 대조 (Task 7)
# ---------------------------------------------------------------------------


# identity 비교에서 빼는 진단 전용 키. crop_ink는 축 ②-b의 신호(크롭이 비었는지)일 뿐
# 무변경 판정 대상이 아니다 — float을 identity에 넣으면 로컬과 macmini의 1 ULP 차이만으로
# DoD 4 게이트가 빨개진다. amount_left(#50, 금액 크롭 좌측 실측)도 진단값이다 — 이 게이트의
# identity는 품목 크롭(boxes·crop_sha)이고, 패치 이전 베이스라인에는 키가 없어 identity에
# 넣으면 경계가 그대로인 잡까지 전부 changed가 된다.
DIAGNOSTIC_KEYS = frozenset({"crop_ink", "amount_left"})


def pair_rows(pairs: list[dict]) -> set[tuple[int, int]]:
    """included 학습쌍의 (job_id, row_index) 집합 — 축 ②-a 모집단."""
    return {(p["job_id"], p["row_index"]) for p in pairs if p["status"] == "included"}


def _identity(entry: dict) -> dict:
    """스냅샷 항목에서 진단 전용 키를 뺀 identity 부분."""
    return {k: v for k, v in entry.items() if k not in DIAGNOSTIC_KEYS}


def _job_key(key: str) -> int:
    """스냅샷 키(job_id 문자열)를 정렬용 정수로 바꾼다.

    `--baseline`은 사용자가 지정하는 외부 JSON이라 키를 신뢰할 수 없다 — 맨 int() 예외 대신
    무엇이 잘못됐는지 말한다.

    Raises:
        ValueError: 키가 job_id 숫자 문자열이 아닐 때.
    """
    try:
        return int(key)
    except (TypeError, ValueError) as e:
        raise ValueError(f"crop-identity 스냅샷 키가 job_id 숫자가 아니다: {key!r}") from e


def snapshot_diff(before: dict, after: dict) -> dict:
    """crop-identity 스냅샷 두 벌을 비교한다(키는 job_id 문자열).

    잡 순서는 사전순이 아니라 job_id 숫자순으로 고정한다 — 산출 순서가 흔들리면 리포트
    diff를 사람이 읽을 수 없다.
    """
    keys = sorted(set(before) | set(after), key=_job_key)
    return {
        "changed": [
            k
            for k in keys
            if k in before and k in after and _identity(before[k]) != _identity(after[k])
        ],
        "missing": [k for k in keys if k not in after],
        "added": [k for k in keys if k not in before],
    }


def _snapshot_entry(snap: dict, key: str) -> dict | None:
    """스냅샷에서 잡 항목을 꺼낸다. 항목이 없으면 None, 스키마가 어긋나면 ValueError.

    `--baseline`은 사용자가 지정하는 외부 JSON이라 신뢰할 수 없다(`_job_key`와 같은 이유) —
    `.get("boxes", [])`로 흡수하면 boxes가 통째로 빠진 항목의 included 행 전부가 vanished도
    moved도 아닌 채 조용히 스킵돼 '변화 0건'으로 보고된다.

    Raises:
        ValueError: 항목에 `boxes`·`crop_sha` 키가 없거나 두 길이가 다를 때.
    """
    entry = snap.get(key)
    if entry is None:
        return None
    missing = {"boxes", "crop_sha"} - entry.keys()
    if missing:
        raise ValueError(f"crop-identity 스냅샷 항목 {key!r}에 {sorted(missing)} 키가 없다")
    if len(entry["boxes"]) != len(entry["crop_sha"]):
        raise ValueError(
            f"crop-identity 스냅샷 항목 {key!r}의 boxes({len(entry['boxes'])})와 "
            f"crop_sha({len(entry['crop_sha'])}) 길이가 다르다"
        )
    return entry


def changed_pairs(before: dict, after: dict, pairs: set[tuple[int, int]]) -> dict:
    """included (job_id, row_index)를 재워프 전후로 대조한다.

    spec §4.3 ②-a가 요구하는 것은 잡 단위가 아니라 행 단위 무변경이다 — snapshot_diff의
    잡 단위 changed만으로는 "그 잡의 어느 행이 움직였는지"를 알 수 없다.

    Returns:
        `{"moved": [...], "vanished": [...]}`. vanished는 after 스냅샷에 그 행 자체가 없는
        경우다(잡이 통째로 빠졌거나 new행 수가 줄었다). 조용히 건너뛰면 폴백이 included
        학습쌍 행을 **없앤** 회귀가 "변화 0건"으로 보고된다.
        before에 없는 행은 대조 기준이 없어 어느 쪽에도 넣지 않는다(잡 단위 added는
        snapshot_diff가 본다).

    Raises:
        ValueError: 스냅샷 항목의 스키마가 어긋날 때(`_snapshot_entry` 계약).
    """
    moved, vanished = [], []
    for job_id, row_index in sorted(pairs):
        b = _snapshot_entry(before, str(job_id))
        if b is None or row_index >= len(b["boxes"]):
            continue
        a = _snapshot_entry(after, str(job_id))
        if a is None or row_index >= len(a["boxes"]):
            vanished.append((job_id, row_index))
            continue
        if (
            b["boxes"][row_index] != a["boxes"][row_index]
            or b["crop_sha"][row_index] != a["crop_sha"][row_index]
        ):
            moved.append((job_id, row_index))
    return {"moved": moved, "vanished": vanished}
