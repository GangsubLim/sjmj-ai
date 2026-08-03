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
from collections import Counter
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
from tools.warp_gate_calib import (
    axis_margins,
    changed_pairs,
    label_of,
    pair_rows,
    render_rewarp_report,
    snapshot_diff,
    stored_vs_rewarp,
)
from tools.warp_gate_rows import (
    STATUS_OK,
    STATUS_UPLOAD_MISSING,
    item_crop,
    job_metrics,
    replicate_rows,
    rewarp_job,
)

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

# crop-identity 스냅샷의 모집단 건전성 하한(재워프 성공 비율). 캐시가 비었거나 fetch가
# 반쪽인 상태로 만든 스냅샷을 베이스라인으로 쓰면, 다음 실행의 실측이 전부 added로만 분류돼
# 게이트가 "변화 0건 + exit 0"을 낸다 — 그 침묵 붕괴를 막는 하한이다. warp 파손 잡의
# 재워프 실패는 정상이므로 품질 임계가 아니라 '캐시가 통째로 얇은가'만 본다.
MIN_OK_RATIO = 0.5


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


def _iter_rewarps(cache: Path, jobs: list[int] | None = None):
    """캐시된 잡을 `(job, status, warped)`로 순회한다 — 재워프 스킵 술어의 단일 소유자.

    지표 리포트(`evaluate_rewarped`)와 crop-identity(`collect_crop_identity`)가 이 순회를
    공유한다. 각자 복제하면 술어 순서가 한쪽만 바뀌었을 때 두 리포트의 모집단이 조용히 갈라진다.
    실패한 잡도 상태와 함께 내보낸다 — 침묵 스킵은 호출자가 모집단을 셀 수 없게 만든다.

    Args:
        cache: fetch가 채운 캐시 디렉터리(jobs.json + uploads/).
        jobs: 대상 job_id 목록. None이면 전 잡.
    """
    all_jobs = json.loads((cache / JOBS_NAME).read_text())
    wanted = set(jobs) if jobs is not None else None
    for j in all_jobs:
        if wanted is not None and j["job_id"] not in wanted:
            continue
        image_path = j.get("image_path")
        if image_path is None:
            yield j, STATUS_UPLOAD_MISSING, None
            continue
        try:
            upload_path = _upload_path(cache, image_path)
        except ValueError:
            # image_path가 uploads/ 밖을 가리키는 경로 탈출 시도다(_upload_path 계약). 예외를
            # 전파하면 전수 리포트가 이 잡 하나 때문에 죽어 앞선 fetch 비용(원본 사진 171MB
            # tar)이 날아간다 — rewarp_job(M3 위 참고)과 동일하게 "예외를 던지지 않는다"는
            # 계약을 지켜 그 잡만 분모 밖으로 강등한다.
            yield j, STATUS_INVALID_IMAGE_PATH, None
            continue
        yield j, *rewarp_job(upload_path)


def evaluate_rewarped(cache: Path, labels: dict) -> list[dict]:
    """원본 사진(주 기준)을 재워프해 게이트 지표를 산출한다. 재워프 실패도 상태로 남긴다.

    Args:
        cache: fetch가 채운 캐시 디렉터리(jobs.json + uploads/ + warped/).
        labels: `{"suspects": {job_id, ...}, "unlabeled": {job_id, ...}}`. 나머지 잡은
            `label_of`가 normal로 흡수한다.
    """
    suspects = set(labels.get("suspects", ()))
    unlabeled = set(labels.get("unlabeled", ()))
    return [
        {
            "job_id": j["job_id"],
            "label": label_of(j["job_id"], suspects, unlabeled),
            "prev_warp_ok": j["warp_ok"],
            "status": status,
            "metrics": job_metrics(warped) if status == STATUS_OK else None,
            # 저장 warped.png는 원본 사진과 무관하게 존재할 수 있어 재워프 성패와 별개로 읽는다
            # (재워프가 실패한 잡은 metrics가 None이라 stored_vs_rewarp가 어차피 건너뛴다).
            "stored_metrics": _stored_metrics(cache, j["job_id"]),
        }
        for j, status, warped in _iter_rewarps(cache)
    ]


# ---------------------------------------------------------------------------
# crop-identity (Task 7) — 재워프 + 행·크롭 재현 스냅샷과 무회귀 대조
# ---------------------------------------------------------------------------


def _dump_crop_pngs(out_dir: Path, job_id: int, warped, boxes: list[list[int]]) -> None:
    """`--dump-crops` 육안검수용 — 잡 하나의 new행 크롭을 `<out_dir>/job-N/row-M.png`로 저장한다.

    크롭 기하는 `warp_gate_rows.item_crop`이 단독 소유한다 — 여기서 다시 계산하면 해시한
    픽셀과 눈으로 보는 PNG가 갈라진다.
    """
    import cv2

    job_dir = out_dir / f"job-{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    for i, box in enumerate(boxes):
        cv2.imwrite(str(job_dir / f"row-{i}.png"), item_crop(warped, box))


def collect_crop_identity(
    cache: Path, jobs: list[int] | None = None, dump_dir: Path | None = None
) -> tuple[dict, dict]:
    """캐시된 잡을 재워프해 crop-identity 스냅샷과 모집단 통계를 만든다.

    crop-identity를 낼 수 없는 잡(사진 없음·경로 탈출·재워프 실패)은 스냅샷에 넣지 않는다 —
    빈 값으로 채우면 다음 실행에서 그 상태(빈 → 실측)가 '변화'로 오탐된다. 대신 스킵을
    **세어서** 함께 돌려준다: 세지 않으면 '아무것도 못 본' 스냅샷과 '정말 변화가 없는'
    스냅샷을 호출자가 구분할 수 없다.

    Args:
        cache: fetch가 채운 캐시 디렉터리.
        jobs: 대상 job_id 목록(생략 시 전 잡).
        dump_dir: 주면 같은 순회 안에서 육안검수 PNG도 저장한다 — 별도 순회를 돌면 재워프
            비용이 두 배가 되고 두 모집단이 갈라질 수 있다.

    Returns:
        `(snapshot, stats)`. snapshot은 `job_id 문자열 → replicate_rows 출력`,
        stats는 `{"total": 대상 잡 수, "ok": 재워프 성공 수, "skipped": {사유: 수}}`.
    """
    snapshot, skipped, n_ok = {}, Counter(), 0
    for j, status, warped in _iter_rewarps(cache, jobs):
        if status != STATUS_OK:
            skipped[status] += 1
            continue
        n_ok += 1
        snap = replicate_rows(warped)
        snapshot[str(j["job_id"])] = snap
        if dump_dir is not None:
            _dump_crop_pngs(dump_dir, j["job_id"], warped, snap["boxes"])
    stats = {"total": n_ok + sum(skipped.values()), "ok": n_ok, "skipped": dict(skipped)}
    return snapshot, stats


def _check_snapshot_population(stats: dict) -> None:
    """스냅샷 모집단을 stdout에 남기고 건전성 하한을 검사한다.

    Raises:
        SystemExit: 스냅샷이 비었거나 재워프 성공 비율이 MIN_OK_RATIO 미만일 때.
    """
    line = f"- 대상 잡 {stats['total']} · 재워프 ok {stats['ok']}"
    if stats["skipped"]:
        line += " · 스킵 " + ", ".join(f"{k} {v}" for k, v in sorted(stats["skipped"].items()))
    print(line)
    if not stats["ok"]:
        raise SystemExit(
            "crop-identity 스냅샷이 비었다 — fetch 캐시(uploads/)를 확인할 것. "
            "이대로 저장하면 다음 실행의 실측이 전부 added로 분류돼 게이트가 조용히 통과한다."
        )
    ratio = stats["ok"] / stats["total"]
    if ratio < MIN_OK_RATIO:
        raise SystemExit(
            f"재워프 성공 {stats['ok']}/{stats['total']}({ratio:.0%})가 하한 "
            f"{MIN_OK_RATIO:.0%} 미만이다 — fetch가 반쪽인지 확인할 것"
        )


def _load_baseline(path: Path) -> dict:
    """`--baseline` 스냅샷 JSON을 읽는다.

    Raises:
        SystemExit: 파일이 없을 때(맨 FileNotFoundError 대신 다음 행동을 지시한다).
    """
    if not path.exists():
        raise SystemExit(f"베이스라인 스냅샷이 없다: {path} — 먼저 --out으로 스냅샷을 만들 것")
    return json.loads(path.read_text())


def _render_pair_impact(before: dict, snapshot: dict, pairs_path: Path) -> list[str]:
    """included 학습쌍(축 ②-a) 행 단위 영향 절을 렌더한다. pairs 캐시가 없으면 빈 목록."""
    if not pairs_path.exists():
        return []
    pairs = json.loads(pairs_path.read_text())
    rows = changed_pairs(before, snapshot, pair_rows(pairs))
    affected = [(j, i, "moved") for j, i in rows["moved"]]
    affected += [(j, i, "vanished") for j, i in rows["vanished"]]
    lines = ["", "## included 학습쌍 영향", ""]
    if not affected:
        return [*lines, "변화 0건"]
    return [
        *lines,
        "| job_id | row_index | 상태 |",
        "| --- | --- | --- |",
        *(f"| {j} | {i} | {s} |" for j, i, s in affected),
    ]


def _run_crop_identity(args) -> None:
    """`crop-identity` 서브커맨드 본체 — 스냅샷 산출·저장, `--baseline` 무회귀 대조.

    Raises:
        SystemExit: `--out` 부모 디렉터리가 없을 때 · 스냅샷 모집단이 건전성 하한 미만일 때 ·
            `--baseline`이 없는 경로일 때 · 잡 단위 `changed`/`missing`이 비어있지 않을 때(code 1).
    """
    if not args.out.parent.exists():
        raise SystemExit(f"--out 부모 디렉터리가 없다: {args.out.parent}")
    snapshot, stats = collect_crop_identity(args.cache, jobs=args.jobs, dump_dir=args.dump_crops)
    # 모집단 검사를 통과한 스냅샷만 파일로 남긴다 — 쓸 수 없는 스냅샷이 디스크에 남으면
    # 그것이 다음 실행의 --baseline이 돼 게이트가 조용히 초록이 된다.
    _check_snapshot_population(stats)
    args.out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1))
    if args.baseline is None:
        print(f"저장: {args.out}")
        return

    before = _load_baseline(args.baseline)
    diff = snapshot_diff(before, snapshot)
    lines = [
        "## crop-identity 대조",
        f"- changed: {diff['changed']}",
        f"- missing: {diff['missing']}",
        f"- added: {diff['added']}",
        *_render_pair_impact(before, snapshot, args.cache / PAIRS_NAME),
    ]
    print("\n".join(lines))
    if diff["changed"] or diff["missing"]:
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    """서브커맨드(fetch/report/crop-identity)를 파싱해 실행한다."""
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
    p_ci = sub.add_parser(
        "crop-identity", help="행·크롭 재현 스냅샷 — 무회귀 대조(changed/missing 시 exit 1)"
    )
    p_ci.add_argument("--out", type=Path, required=True, help="스냅샷 JSON 출력 경로")
    p_ci.add_argument("--baseline", type=Path, default=None, help="비교할 이전 스냅샷 JSON")
    p_ci.add_argument("--dump-crops", type=Path, default=None, help="크롭 PNG를 저장할 디렉터리")
    # nargs="+" — `--jobs`만 주고 id를 빠뜨리면 대상을 좁히려던 의도와 정반대로 전 잡을 돈다.
    p_ci.add_argument("--jobs", type=int, nargs="+", default=None, help="대상 잡 id(생략 시 전체)")
    args = ap.parse_args(argv)

    if args.cmd == "fetch":
        meta = fetch_all(args.host, env_or(ENV_BACKEND_ENV), env_or(ENV_WORKER_ENV), args.cache)
        print(
            f"동기화 완료 → {args.cache} (잡 {meta['n_jobs']} · warped {meta['n_warped']} · "
            f"uploads {meta['n_uploads']} · pairs {meta['n_pairs']})"
        )
        return

    if args.cmd == "crop-identity":
        _run_crop_identity(args)
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
