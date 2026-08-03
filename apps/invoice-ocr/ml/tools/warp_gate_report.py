"""warp 정합 게이트 캘리브레이션 리포트 도구.

배포 서버(macmini)의 **전체** ocr_jobs와 잡별 warped.png·원본 사진(uploads)·학습쌍(pairs)을
동기화해, 게이트 지표 4종과 판정을 전수 산출한다. 임계 선정 근거·무회귀(acceptance 3) 입증·
향후 임계 재조정에 쓴다.

curation_report의 fetch는 training_pairs가 있는 잡만 조회하므로(50잡 중 15잡) 여기서는
자체 전수 fetch를 쓴다. 원격 접속값은 tools.remote의 env 관례를 그대로 재사용하며
데이터 루트는 원격 worker env의 SJMJ_DATA_DIR에서 읽는다(하드코딩 금지).

Usage:
    uv run python -m tools.warp_gate_report fetch                      # 전 잡 + uploads/pairs/warped.png 동기화
    uv run python -m tools.warp_gate_report report --suspect 34 38 39 --unlabeled 2 3 \
        --max-job-id 63                                                # 재워프 지표·마진 md + json
"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from tools.cache_sync import (
    invalidate_manifest,
    load_cache_meta,
    sync_remote_files,
    write_manifest,
)
from tools.curation_enrich import PAIRS_SQL, parse_pairs_tsv
from tools.remote import (
    ENV_BACKEND_ENV,
    ENV_SSH_HOST,
    ENV_WORKER_ENV,
    env_or,
    mysql_script,
    run_ssh,
)
from tools.warp_gate_calib import axis_margins, label_of, render_rewarp_report, stored_vs_rewarp
from tools.warp_gate_rows import STATUS_OK, STATUS_UPLOAD_MISSING, job_metrics, rewarp_job

ML_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ML_ROOT / "results" / "warp_gate"

JOBS_NAME = "jobs.json"
WARPED_GLOB = "job-*/warped.png"
UPLOADS_ROOT = "ocr_uploads"
UPLOADS_GLOB = "*"
PAIRS_NAME = "pairs.json"
UPLOADS_TIMEOUT_S = 3600.0  # 원본 사진 171MB tar 전송 — run_ssh 기본 600초로는 부족할 수 있다

# 전수 조회 — result_json 통째가 아니라 warp_ok 한 값만 서버에서 뽑는다. warp_ok 값 자체는
# true/false/NULL뿐이라 경계 오염 여지가 없지만, 자유형 image_path(VARCHAR(512))가 업로드
# 파일명 suffix를 물려받아 탭이 섞일 수 있어(tools/curation_enrich.py:79 참조) TSV 컬럼 경계
# 오염 여지가 다시 생겼다. 로컬 JSON 파싱(=파싱 실패를 None으로 삼키던 자리)은 여전히 없고,
# 열 수 검사(parse_job_rows_tsv)가 조용한 밀림 대신 즉시 ValueError로 실패시킨다.
JOBS_SQL = "SELECT id, result_json->>'$.warp_ok', image_path FROM ocr_jobs ORDER BY id"

STATUS_INVALID_IMAGE_PATH = "invalid_image_path"  # image_path가 uploads/ 밖을 가리키는 시도


# ---------------------------------------------------------------------------
# 순수 계층 (IO 없음 — 단위테스트 대상)
# ---------------------------------------------------------------------------


# mysql --batch가 warp_ok 열에 낼 수 있는 표현의 전부. NULL은 result_json 자체가 NULL이거나
# warp_ok 키가 없는 잡(미처리·failed)이며, 빈값은 마지막 줄 개행 처리에서 온다.
WARP_OK_VALUES = {"true": True, "false": False, "NULL": None, "": None}


def parse_job_rows_tsv(text: str) -> list[dict]:
    """mysql --batch TSV(id, warp_ok, image_path)를 레코드 리스트로 파싱한다.

    Raises:
        ValueError: 열 수가 헤더와 다르거나 warp_ok가 예상 밖 표현일 때. 예상 밖 값을 None으로
            흡수하면 무회귀 분모가 조용히 줄어 캘리브 결론이 왜곡되므로 fail-fast한다.
    """
    lines = text.strip().split("\n")
    # 열 수는 헤더에서 파생한다 — JOBS_SQL의 프로젝션 열 수가 바뀌면 이 검사도 함께 움직인다.
    n_cols = len(lines[0].split("\t"))
    out = []
    for ln in lines[1:]:
        if not ln.strip():
            continue
        cells = ln.split("\t")
        if len(cells) != n_cols:
            raise ValueError(f"jobs TSV 열 수가 {n_cols}이 아니다({len(cells)}): {ln!r}")
        job_id, raw, image_path = cells
        raw = raw.strip()
        if raw not in WARP_OK_VALUES:
            raise ValueError(f"warp_ok 값이 예상 밖이다(job_id={job_id}): {raw!r}")
        image_path = image_path.strip()
        out.append(
            {
                "job_id": int(job_id),
                "warp_ok": WARP_OK_VALUES[raw],
                # image_path는 nullable이다 — mysql --raw는 NULL을 리터럴 'NULL'로 준다. 빈
                # 문자열도 missing으로 합류시킨다(warp_ok가 ""를 None으로 접는 것과 대칭 —
                # 안 그러면 "사진 없음"이 "사진 있음(경로 빈값)"으로 오분류된다).
                "image_path": None if image_path in ("NULL", "") else image_path,
            }
        )
    return out


# ---------------------------------------------------------------------------
# ssh fetch 글루 (원격 접속 — 단위테스트 비대상)
# ---------------------------------------------------------------------------


def fetch_all(host: str, backend_env: str, worker_env: str, cache: Path) -> dict:
    """전체 ocr_jobs·training_pairs와 원본 사진(주 기준)·warped.png(참고 축)를 동기화한다."""
    cache.mkdir(parents=True, exist_ok=True)
    # 중단 시 '빈(또는 반쪽) warped + 옛 meta'라는 하이브리드 캐시가 남지 않도록 먼저
    # 무효화한다 — 남으면 report가 옛 잡 목록으로 성공하고 옛 fetched_at을 동기화 시각으로
    # 찍는다. 이 도구의 산출은 게이트 임계의 근거다(blank_crop_report.fetch_all과 동일).
    invalidate_manifest(cache, JOBS_NAME)
    (cache / PAIRS_NAME).unlink(missing_ok=True)
    jobs = parse_job_rows_tsv(run_ssh(host, mysql_script(backend_env, JOBS_SQL, raw=True)).decode())
    pairs = parse_pairs_tsv(run_ssh(host, mysql_script(backend_env, PAIRS_SQL, raw=False)).decode())
    warped = sync_remote_files(host, worker_env, pattern=WARPED_GLOB, dest=cache / "warped")
    uploads = sync_remote_files(
        host,
        worker_env,
        pattern=UPLOADS_GLOB,
        dest=cache / "uploads",
        root=UPLOADS_ROOT,
        timeout=UPLOADS_TIMEOUT_S,
    )
    (cache / PAIRS_NAME).write_text(json.dumps(pairs, ensure_ascii=False, indent=1))
    meta = write_manifest(
        cache,
        JOBS_NAME,
        jobs,
        host=host,
        counts={
            "n_jobs": len(jobs),
            "n_warped": len(warped),
            "n_uploads": len(uploads),
            "n_pairs": len(pairs),
        },
    )
    if meta["n_jobs"] > 0 and meta["n_warped"] == 0:
        print(
            f"⚠️  잡 {meta['n_jobs']}건인데 warped.png가 0건이다 — "
            f"SJMJ_DATA_DIR({host}:{worker_env})를 확인할 것. 리포트는 전 잡을 warp_missing으로 찍는다."
        )
    if meta["n_jobs"] > 0 and meta["n_uploads"] == 0:
        print(
            f"⚠️  잡 {meta['n_jobs']}건인데 원본 사진이 0건이다 — "
            f"SJMJ_DATA_DIR/{UPLOADS_ROOT}({host}:{worker_env})를 확인할 것. "
            "재워프(주 기준) 산출이 전부 upload_missing이 된다."
        )
    return meta


def _upload_path(cache: Path, image_path: str) -> Path:
    """image_path(자유형 VARCHAR)에서 파일명만 취해 로컬 uploads 캐시와 조인한다.

    macmini 절대경로(`/data/ocr_uploads/ab12.jpg`)를 로컬 캐시(`uploads/ab12.jpg`)에 매핑한다.
    `image_path`는 신뢰할 수 없는 자유형 컬럼(DB `VARCHAR(512)`)이므로 basename만 취해
    절대경로·`..` 세그먼트가 캐시 밖으로 벗어나지 못하게 막는다.

    Raises:
        ValueError: `Path(image_path).name`이 비었거나 `.`/`..`뿐일 때(경로 탈출 시도).
    """
    name = Path(image_path).name
    if not name or name in (".", ".."):
        raise ValueError(f"image_path에서 안전한 파일명을 얻을 수 없다: {image_path!r}")
    return cache / "uploads" / name


def _stored_metrics(cache: Path, job_id: int) -> dict | None:
    """저장 warped.png(참고 축)가 있으면 표준 마스크 지표를 뽑는다. 없으면 None."""
    png = cache / "warped" / f"job-{job_id}" / "warped.png"
    if not png.exists():
        return None
    import cv2

    from handwriting.warp_gate import compute_metrics

    img = cv2.imread(str(png))
    if img is None:
        return None
    return asdict(compute_metrics(img))


def evaluate_rewarped(cache: Path, labels: dict) -> list[dict]:
    """원본 사진(주 기준)을 재워프해 게이트 지표를 산출한다. 재워프 실패도 상태로 남긴다.

    Args:
        cache: fetch가 채운 캐시 디렉터리(jobs.json + uploads/ + warped/).
        labels: `{"suspects": {job_id, ...}, "unlabeled": {job_id, ...}}`. 나머지 잡은
            `label_of`가 normal로 흡수한다.
    """
    suspects = set(labels.get("suspects", ()))
    unlabeled = set(labels.get("unlabeled", ()))
    jobs = json.loads((cache / JOBS_NAME).read_text())
    records = []
    for j in jobs:
        job_id = j["job_id"]
        base = {
            "job_id": job_id,
            "label": label_of(job_id, suspects, unlabeled),
            "prev_warp_ok": j["warp_ok"],
        }
        image_path = j.get("image_path")
        if image_path is None:
            records.append(
                {**base, "status": STATUS_UPLOAD_MISSING, "metrics": None, "stored_metrics": None}
            )
            continue
        try:
            upload_path = _upload_path(cache, image_path)
        except ValueError:
            # image_path가 uploads/ 밖을 가리키는 경로 탈출 시도다(_upload_path 계약). 예외를
            # 전파하면 전수 리포트가 이 잡 하나 때문에 죽어 앞선 fetch 비용(원본 사진 171MB
            # tar)이 날아간다 — rewarp_job(M3 위 참고)과 동일하게 "예외를 던지지 않는다"는
            # 계약을 지켜 그 잡만 분모 밖으로 강등한다.
            records.append(
                {
                    **base,
                    "status": STATUS_INVALID_IMAGE_PATH,
                    "metrics": None,
                    "stored_metrics": None,
                }
            )
            continue
        status, warped = rewarp_job(upload_path)
        metrics = job_metrics(warped) if status == STATUS_OK else None
        records.append(
            {
                **base,
                "status": status,
                "metrics": metrics,
                "stored_metrics": _stored_metrics(cache, job_id),
            }
        )
    return records


def main(argv: list[str] | None = None) -> None:
    """서브커맨드(fetch/report)를 파싱해 실행한다."""
    ap = argparse.ArgumentParser(prog="warp_gate_report", description=__doc__)
    ap.add_argument("--host", default=env_or(ENV_SSH_HOST), help="ssh 호스트(별칭)")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="로컬 캐시 디렉터리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser(
        "fetch", help="전체 ocr_jobs + 원본 사진(uploads) + 학습쌍(pairs) + warped.png 동기화"
    )
    p_rep = sub.add_parser(
        "report", help="원본 재워프 평가 → warp_gate_report.md + warp_gate_metrics.json"
    )
    p_rep.add_argument("--suspect", type=int, nargs="*", default=[], help="warp 의심 잡 id")
    p_rep.add_argument("--unlabeled", type=int, nargs="*", default=[], help="육안 미확인 잡 id")
    p_rep.add_argument(
        "--max-job-id",
        type=int,
        default=None,
        help="모집단 상한 — 초과 id는 리포트·마진 계산에서 전부 제외(캘리브 도중 신규 잡 오염 방지)",
    )
    args = ap.parse_args(argv)

    if args.cmd == "fetch":
        meta = fetch_all(args.host, env_or(ENV_BACKEND_ENV), env_or(ENV_WORKER_ENV), args.cache)
        print(
            f"동기화 완료 → {args.cache} (잡 {meta['n_jobs']} · warped {meta['n_warped']} · "
            f"uploads {meta['n_uploads']} · pairs {meta['n_pairs']})"
        )
        return

    meta = load_cache_meta(args.cache, JOBS_NAME, tool="warp_gate_report")
    labels = {"suspects": set(args.suspect), "unlabeled": set(args.unlabeled)}
    records = evaluate_rewarped(args.cache, labels)
    if args.max_job_id is not None:
        records = [r for r in records if r["job_id"] <= args.max_job_id]
    margins = {"std": axis_margins(records, "std"), "enh": axis_margins(records, "enh")}
    drift = stored_vs_rewarp(records)
    md = render_rewarp_report(records, margins, drift, meta)
    out_md = args.cache / "warp_gate_report.md"
    out_md.write_text(md)
    metrics_json = [
        {"job_id": r["job_id"], "label": r["label"], "status": r["status"], "metrics": r["metrics"]}
        for r in records
    ]
    out_json = args.cache / "warp_gate_metrics.json"
    out_json.write_text(json.dumps(metrics_json, ensure_ascii=False, indent=1))
    print(md)
    print(f"저장: {out_md} · {out_json}")


if __name__ == "__main__":
    main()
