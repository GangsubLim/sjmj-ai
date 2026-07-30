"""warp 정합 게이트 캘리브레이션 리포트 도구.

배포 서버(macmini)의 **전체** ocr_jobs와 잡별 warped.png를 동기화해, 게이트 지표 4종과
판정을 전수 산출한다. 임계 선정 근거·무회귀(acceptance 3) 입증·향후 임계 재조정에 쓴다.

curation_report의 fetch는 training_pairs가 있는 잡만 조회하므로(50잡 중 15잡) 여기서는
자체 전수 fetch를 쓴다. 원격 접속값은 tools.remote의 env 관례를 그대로 재사용하며
데이터 루트는 원격 worker env의 SJMJ_DATA_DIR에서 읽는다(하드코딩 금지).

Usage:
    uv run python -m tools.warp_gate_report fetch                      # 전 잡 + warped.png 동기화
    uv run python -m tools.warp_gate_report report --suspect 34 38 39  # 지표·판정 일람 md
"""

import argparse
import json
from collections.abc import Callable
from dataclasses import asdict, fields
from pathlib import Path

from handwriting.warp_gate import WarpGateMetrics, blue_asymmetry
from tools.cache_sync import (
    invalidate_manifest,
    load_cache_meta,
    sync_remote_files,
    write_manifest,
)
from tools.remote import (
    ENV_BACKEND_ENV,
    ENV_SSH_HOST,
    ENV_WORKER_ENV,
    env_or,
    mysql_script,
    run_ssh,
)

ML_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ML_ROOT / "results" / "warp_gate"

JOBS_NAME = "jobs.json"
WARPED_GLOB = "job-*/warped.png"

# 전수 조회 — result_json 통째가 아니라 warp_ok 한 값만 서버에서 뽑는다. 값이 true/false/NULL
# 뿐이라 TSV 컬럼 경계 오염(제어문자·개행) 여지가 원천 소멸하고 전송량도 줄며, 로컬 JSON
# 파싱(=파싱 실패를 None으로 삼키던 자리)이 아예 없어진다.
JOBS_SQL = "SELECT id, result_json->>'$.warp_ok' FROM ocr_jobs ORDER BY id"

STATUS_GATE_TARGET = "gate_target"
STATUS_QUAD_MISSING = "quad_missing"
STATUS_WARP_MISSING = "warp_missing"
STATUS_WARP_UNREADABLE = "warp_unreadable"  # 파일은 있으나 cv2.imread가 None (손상/권한)


# ---------------------------------------------------------------------------
# 순수 계층 (IO 없음 — 단위테스트 대상)
# ---------------------------------------------------------------------------


# mysql --batch가 warp_ok 열에 낼 수 있는 표현의 전부. NULL은 result_json 자체가 NULL이거나
# warp_ok 키가 없는 잡(미처리·failed)이며, 빈값은 마지막 줄 개행 처리에서 온다.
WARP_OK_VALUES = {"true": True, "false": False, "NULL": None, "": None}


def parse_job_rows_tsv(text: str) -> list[dict]:
    """mysql --batch TSV(id, warp_ok)를 [{job_id, warp_ok}]로 파싱한다.

    Raises:
        ValueError: warp_ok 열이 위 표현이 아닐 때. 예상 밖 값을 None으로 흡수하면 무회귀
            분모가 조용히 줄어 캘리브 결론이 왜곡되므로 fail-fast한다(curation_report 관례).
    """
    out = []
    for ln in text.strip().split("\n")[1:]:
        if not ln.strip():
            continue
        job_id, _, raw = ln.partition("\t")
        raw = raw.strip()
        if raw not in WARP_OK_VALUES:
            raise ValueError(f"warp_ok 값이 예상 밖이다(job_id={job_id}): {raw!r}")
        out.append({"job_id": int(job_id), "warp_ok": WARP_OK_VALUES[raw]})
    return out


def job_status(warp_ok: bool | None, has_warped: bool) -> str:
    """게이트 평가 분모 안/밖을 분류한다."""
    if has_warped:
        return STATUS_GATE_TARGET
    if warp_ok is False:
        return STATUS_QUAD_MISSING
    return STATUS_WARP_MISSING


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


def metric_value(metrics: dict, key: str) -> float:
    """지표 dict에서 표에 쓸 값을 뽑는다(파생 지표는 게이트 술어와 동일한 식으로 계산)."""
    derive = DERIVED_METRICS.get(key)
    return derive(metrics) if derive else metrics[key]


def metric_margins(records: list[dict]) -> dict:
    """지표별 정상군 최악값 vs 의심군 최선값과 그 분리 마진(%)을 계산한다.

    임계 선정의 근거다(Task 7 합격 기준 3·4). 마진이 음수면 두 분포가 겹친 것이므로
    그 지표에는 판정을 싣지 않는다.
    """
    targets = [r for r in records if r["status"] == STATUS_GATE_TARGET and r["metrics"]]
    normal = [r["metrics"] for r in targets if not r["suspect"]]
    suspect = [r["metrics"] for r in targets if r["suspect"]]
    out = {}
    for k in METRIC_KEYS:
        if not normal or not suspect:
            out[k] = None
            continue
        nv = [metric_value(m, k) for m in normal]
        sv = [metric_value(m, k) for m in suspect]
        higher_is_better = k in HIGHER_IS_BETTER
        # 정상군 '최악' = 실패에 가장 가까운 값, 의심군 '최선' = 통과에 가장 가까운 값.
        worst_normal, best_suspect = (min(nv), max(sv)) if higher_is_better else (max(nv), min(sv))
        gap = worst_normal - best_suspect if higher_is_better else best_suspect - worst_normal
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


def summarize_gate(records: list[dict]) -> dict:
    """분모 집계와 판정 결과, 회귀(이전에 warp_ok=true였는데 실패)를 계산한다."""
    targets = [r for r in records if r["status"] == STATUS_GATE_TARGET]
    fails = [r for r in targets if r["gate_pass"] is False]
    # acceptance 3의 문언은 '기존 warp 정상 잡(true 유지)'이다. prev_warp_ok가 None(미처리·
    # failed)이거나 False(이미 강등)인 잡은 애초에 true였던 적이 없어 무회귀 판단 대상이 아니다.
    return {
        "n_total": len(records),
        "n_gate_target": len(targets),
        "n_quad_missing": sum(r["status"] == STATUS_QUAD_MISSING for r in records),
        "n_warp_missing": sum(r["status"] == STATUS_WARP_MISSING for r in records),
        "n_unreadable": sum(r["status"] == STATUS_WARP_UNREADABLE for r in records),
        "n_pass": sum(r["gate_pass"] is True for r in targets),
        "n_fail": len(fails),
        "n_prev_true": sum(r["prev_warp_ok"] is True for r in targets),
        # 무회귀 문장의 분모 — regressions(분자)와 **같은 집합**이어야 한다(suspect 제외).
        # n_prev_true를 분모로 쓰면 의도적으로 강등한 suspect가 분모에만 남아 문장이 거짓이 된다.
        "n_regression_denom": sum(
            1 for r in targets if not r["suspect"] and r["prev_warp_ok"] is True
        ),
        "n_suspect": sum(r["suspect"] for r in targets),
        "n_suspect_demoted": sum(r["suspect"] for r in fails),
        "regressions": sorted(
            r["job_id"] for r in fails if not r["suspect"] and r["prev_warp_ok"] is True
        ),
        "unknown_fail": sorted(
            r["job_id"] for r in fails if not r["suspect"] and r["prev_warp_ok"] is not True
        ),
        "margins": metric_margins(records),
    }


def _render_margin_table(margins: dict) -> list[str]:
    """지표별 분리 마진 표를 렌더한다(게이트 술어가 보는 파생 지표 2종 포함)."""
    rows = []
    for k, v in margins.items():
        if not v:
            rows.append(f"| {k} | — | — | — | (정상군 또는 의심군이 비어 계산 불가) |")
            continue
        rows.append(
            f"| {k} | {v['worst_normal']:.4f} | {v['best_suspect']:.4f} | "
            f"{v['gap']:.4f} | {v['margin_pct']:.1f}%{'*' if v['denom_fallback'] else ''} |"
        )
    lines = [
        "## 지표별 분리 마진 (임계 선정 근거)",
        "",
        "원시 L·R과 함께 게이트 술어가 실제로 보는 파생 지표 2종을 싣는다 — "
        "`blue_ratio_min`=min(L,R)은 ③ 규칙(MIN_BLUE_RATIO), `blue_asym`은 ④ 규칙"
        "(MAX_BLUE_ASYMMETRY)에 대응한다.",
        "",
        "| 지표 | 정상군 최악값 | 의심군 최선값 | gap | 마진% |",
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


def _render_suspect_coverage(s: dict, meta: dict, records: list[dict]) -> list[str]:
    """요청 suspect id의 반영 현황(평가 대상 수 · jobs에 없는 id)을 렌더한다."""
    requested = meta.get("suspects")
    if requested is None:
        return []
    unknown = sorted(set(requested) - {r["job_id"] for r in records})
    line = f"- 요청 suspect {len(requested)}건 중 평가 대상 {s['n_suspect']}건"
    if unknown:
        line += f" · ⚠️ jobs에 없는 id {unknown}(오타 또는 다른 서버 의심)"
    return [line]


def _render_cache_warning(s: dict, meta: dict) -> list[str]:
    """fetch가 센 warped 수와 이미지가 있는 잡 수가 어긋나면 stale 캐시 경고를 남긴다."""
    n_warped = meta.get("n_warped")
    n_with_image = s["n_gate_target"] + s["n_unreadable"]
    if n_warped is None or n_warped == n_with_image:
        return []
    return [
        f"- ⚠️ 캐시 불일치 — fetch 시 warped {n_warped}건인데 이미지가 있는 잡은 "
        f"{n_with_image}건이다. 지표가 옛 워프에서 나왔을 수 있으니 fetch를 다시 실행할 것."
    ]


def render_gate_report(records: list[dict], meta: dict) -> str:
    """지표·판정 일람과 분모 집계를 마크다운으로 렌더한다."""
    s = summarize_gate(records)
    lines = [
        "# warp 정합 게이트 캘리브레이션 리포트",
        "",
        f"- 동기화: {meta.get('fetched_at', '?')} · 호스트 {meta.get('host', '?')}",
        f"- 전체 잡 {s['n_total']} = 게이트 평가 대상 {s['n_gate_target']} + "
        f"quad_missing {s['n_quad_missing']} + warp_missing {s['n_warp_missing']} + "
        f"warp_unreadable {s['n_unreadable']}",
        f"- 판정: pass {s['n_pass']} · fail {s['n_fail']} "
        f"(warp_suspect {s['n_suspect_demoted']}/{s['n_suspect']} 강등)",
        f"- 무회귀 확인 — 이전 warp_ok=true 정상 잡(suspect 제외) {s['n_regression_denom']} 중 "
        f"실패(회귀): {s['regressions'] or '없음'}",
        f"- 분모 밖 실패(이전 warp_ok가 true가 아니던 잡): {s['unknown_fail'] or '없음'}",
        *_render_suspect_coverage(s, meta, records),
        *_render_cache_warning(s, meta),
        "",
        *_render_margin_table(s["margins"]),
        "",
        "## 게이트 평가 대상 지표",
        "",
        "| job | hline | pitch_dev | blue_L | blue_R | 판정 | 이전 warp_ok | suspect |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in records:
        if r["status"] != STATUS_GATE_TARGET:
            continue
        m = r["metrics"]
        lines.append(
            f"| {r['job_id']} | {m['hline_count']} | {m['pitch_dev']:.3f} | "
            f"{m['blue_ratio_left']:.4f} | {m['blue_ratio_right']:.4f} | "
            f"{'pass' if r['gate_pass'] else 'FAIL'} | {r['prev_warp_ok']} | "
            f"{'Y' if r['suspect'] else '—'} |"
        )

    excluded = [r for r in records if r["status"] != STATUS_GATE_TARGET]
    lines += ["", "## 분모 제외 (게이트 무관)", ""]
    if excluded:
        lines += [
            f"- job {r['job_id']}: {r['status']} (result_json warp_ok={r['prev_warp_ok']})"
            f"{' · suspect 요청됨' if r['suspect'] else ''}"
            for r in excluded
        ]
    else:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# ssh fetch 글루 (원격 접속 — 단위테스트 비대상)
# ---------------------------------------------------------------------------


def fetch_all(host: str, backend_env: str, worker_env: str, cache: Path) -> dict:
    """전체 ocr_jobs 목록과 잡별 warped.png를 캐시로 동기화한다."""
    cache.mkdir(parents=True, exist_ok=True)
    # 중단 시 '빈(또는 반쪽) warped + 옛 meta'라는 하이브리드 캐시가 남지 않도록 먼저
    # 무효화한다 — 남으면 report가 옛 잡 목록으로 성공하고 옛 fetched_at을 동기화 시각으로
    # 찍는다. 이 도구의 산출은 게이트 임계의 근거다(blank_crop_report.fetch_all과 동일).
    invalidate_manifest(cache, JOBS_NAME)
    jobs = parse_job_rows_tsv(run_ssh(host, mysql_script(backend_env, JOBS_SQL, raw=True)).decode())
    names = sync_remote_files(host, worker_env, pattern=WARPED_GLOB, dest=cache / "warped")
    meta = write_manifest(
        cache, JOBS_NAME, jobs, host=host, counts={"n_jobs": len(jobs), "n_warped": len(names)}
    )
    if meta["n_jobs"] > 0 and meta["n_warped"] == 0:
        print(
            f"⚠️  잡 {meta['n_jobs']}건인데 warped.png가 0건이다 — "
            f"SJMJ_DATA_DIR({host}:{worker_env})를 확인할 것. 리포트는 전 잡을 warp_missing으로 찍는다."
        )
    return meta


def evaluate_cached(
    cache: Path, suspects: set[int], *, imread: Callable[[str], object] | None = None
) -> list[dict]:
    """캐시된 warped.png에 게이트를 적용해 record 리스트를 만든다.

    Args:
        cache: fetch가 채운 캐시 디렉터리.
        suspects: warp 의심 잡 id 집합(무회귀 분모에서 빼고 강등 여부를 따로 센다).
        imread: 이미지 리더 주입구(테스트의 Fake 어댑터). 기본값 None이면 이때만 cv2를
            import해 `cv2.imread`를 쓴다 — 덕분에 이 평가 경로를 코어 venv에서도 테스트한다.
    """
    from handwriting.warp_gate import compute_metrics, evaluate_warp

    if imread is None:
        import cv2

        imread = cv2.imread

    jobs = json.loads((cache / JOBS_NAME).read_text())
    records = []
    for j in jobs:
        png = cache / "warped" / f"job-{j['job_id']}" / "warped.png"
        status = job_status(j["warp_ok"], png.exists())
        metrics = gate_pass = None
        if status == STATUS_GATE_TARGET:
            # imread는 손상/권한 문제에서 예외 없이 None을 준다 → blue_mask(None)이
            # ValueError로 전수 리포트를 중간에 죽인다(앞의 fetch 비용이 날아간다).
            img = imread(str(png))
            if img is None:
                status = STATUS_WARP_UNREADABLE
            else:
                m = compute_metrics(img)
                metrics, gate_pass = asdict(m), evaluate_warp(m)
        records.append(
            {
                "job_id": j["job_id"],
                "status": status,
                "prev_warp_ok": j["warp_ok"],
                "suspect": j["job_id"] in suspects,
                "metrics": metrics,
                "gate_pass": gate_pass,
            }
        )
    return records


def main(argv: list[str] | None = None) -> None:
    """서브커맨드(fetch/report)를 파싱해 실행한다."""
    ap = argparse.ArgumentParser(prog="warp_gate_report", description=__doc__)
    ap.add_argument("--host", default=env_or(ENV_SSH_HOST), help="ssh 호스트(별칭)")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="로컬 캐시 디렉터리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="전체 ocr_jobs + warped.png 동기화")
    p_rep = sub.add_parser("report", help="캐시 평가 → warp_gate_report.md")
    p_rep.add_argument("--suspect", type=int, nargs="*", default=[], help="warp 의심 잡 id")
    args = ap.parse_args(argv)

    if args.cmd == "fetch":
        meta = fetch_all(args.host, env_or(ENV_BACKEND_ENV), env_or(ENV_WORKER_ENV), args.cache)
        print(f"동기화 완료 → {args.cache} (잡 {meta['n_jobs']} · warped {meta['n_warped']})")
        return

    meta = load_cache_meta(args.cache, JOBS_NAME, tool="warp_gate_report")
    records = evaluate_cached(args.cache, set(args.suspect))
    # 요청한 suspect 목록을 meta에 실어 리포트가 "요청 n건 중 평가 m건"과 미지 id를 밝히게 한다.
    md = render_gate_report(records, {**meta, "suspects": sorted(set(args.suspect))})
    out = args.cache / "warp_gate_report.md"
    out.write_text(md)
    print(md)
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
