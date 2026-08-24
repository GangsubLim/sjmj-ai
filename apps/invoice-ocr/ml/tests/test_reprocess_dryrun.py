"""tools.reprocess_dryrun — 무커밋 재처리 드라이런(Issue #100).

순수 계층(집계·렌더·jsonl)은 여기서 직접 단위테스트하고, 글루(forecast_job·main)는
인메모리 sqlite 엔진 + Fake 추론으로 모델 없이 돈다(tests/test_worker_db.py 관례).
"""

import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from handwriting.amount_read import DegenerateOutputError
from tests.conftest import import_scopes
from tools import reprocess_dryrun as rd
from tools.reprocess_dryrun import (
    BatchSummary,
    JobForecast,
    RunMeta,
    forecast_job,
    meta_line,
    parse_done,
    parse_meta,
    record_line,
    render,
    summarize,
)
from worker.db import WorkerQueue


def _fc(job_id=1, *, rows=3, pairs=3, relinked=3, orphaned=0, error=None):
    return JobForecast(
        job_id=job_id,
        new_row_count=rows,
        pair_count=pairs,
        relinked=relinked,
        orphaned=orphaned,
        error=error,
    )


def test_summarize_adds_up_jobs_pairs_and_the_orphan_ratio():
    summary = summarize(
        [
            _fc(27, rows=14, pairs=11, relinked=11, orphaned=0),
            _fc(40, rows=0, pairs=9, relinked=0, orphaned=9),
        ]
    )
    assert summary == BatchSummary(
        job_count=2, pair_count=20, relinked=11, orphaned=9, orphan_ratio=0.45, failed=0
    )


def test_summarize_defends_a_batch_with_no_pairs_at_all():
    # 신규 잡만 고른 배치는 분모가 0이다 — ZeroDivisionError로 죽으면 안 된다.
    assert summarize([_fc(1, rows=0, pairs=0, relinked=0, orphaned=0)]).orphan_ratio == 0.0
    assert summarize([]).orphan_ratio == 0.0


def test_a_failed_job_leaves_the_orphan_denominator():
    """예측하지 못한 잡을 '미결 0'으로 세면 배치가 실제보다 안전해 보인다(spec §5)."""
    summary = summarize(
        [
            _fc(27, rows=10, pairs=10, relinked=8, orphaned=2),
            _fc(51, rows=0, pairs=23, relinked=0, orphaned=0, error="RuntimeError: warp 실패"),
        ]
    )
    assert summary.job_count == 1
    assert summary.failed == 1
    assert summary.pair_count == 10, "실패 잡의 pair는 분모에 들어가지 않는다"
    assert summary.orphan_ratio == 0.2


def test_render_shows_the_failed_jobs_pairs_outside_the_denominator():
    forecasts = [
        _fc(27, rows=14, pairs=11, relinked=11, orphaned=0),
        _fc(51, rows=0, pairs=23, relinked=0, orphaned=0, error="warp 실패"),
    ]
    out = render(forecasts, summarize(forecasts))

    assert "예측 불가" in out
    assert "warp 실패" in out
    assert "23" in out, "분모에서 빠지는 규모가 보이지 않으면 비율이 몇 건을 대변하는지 알 수 없다"
    assert "(잡 1건" in out


def test_render_prints_the_batch_total_line():
    forecasts = [
        _fc(27, rows=14, pairs=11, relinked=11, orphaned=0),
        _fc(40, rows=6, pairs=9, relinked=0, orphaned=9),
    ]
    out = render(forecasts, summarize(forecasts))

    assert "합계" in out
    assert "20" in out  # pairs 합
    assert "45.0%" in out  # 미결 비율
    assert "(잡 2건" in out


def test_records_round_trip_through_the_jsonl():
    forecasts = [_fc(27), _fc(51, error="degenerate")]
    lines = [record_line(f) for f in forecasts]
    assert parse_done(lines) == {27: forecasts[0], 51: forecasts[1]}


def test_parse_done_skips_the_meta_line_and_blanks():
    meta = RunMeta(job_ids=(27, 40), code_version="abc123def456")
    lines = [meta_line(meta), "", record_line(_fc(27))]
    assert list(parse_done(lines)) == [27]


def test_meta_round_trips_including_a_missing_code_version():
    for meta in (RunMeta(job_ids=(27, 40), code_version="abc123def456"), RunMeta((), None)):
        assert parse_meta(meta_line(meta)) == meta


# ---------------------------------------------------------------------------
# 실행형 픽스처 — 인메모리 sqlite(tests/test_worker_db.py 관례).
# 드라이런 전후 "행의 최종 상태"를 그대로 비교하는 것이 무변경 단언의 근거다.
# ---------------------------------------------------------------------------

_SCHEMA = (
    "CREATE TABLE ocr_jobs (id INTEGER PRIMARY KEY, status TEXT, image_path TEXT, "
    "result_json TEXT, curation_reviewed INTEGER DEFAULT 1)",
    "CREATE TABLE training_pairs (id INTEGER PRIMARY KEY, job_id INTEGER, "
    "crop_ref TEXT UNIQUE, row_index INTEGER, supply INTEGER, draft_supply INTEGER, "
    "status TEXT, exclusion_reason TEXT, reviewed_at TEXT)",
)

RESULT = {
    "rows": [{"row_index": 0, "supply": 3000}, {"row_index": 1, "supply": 5000}],
    "warp_ok": True,
}


def _engine(jobs=(27,), pairs_per_job=2):
    """잡 몇 건 + 잡마다 확정 쌍 몇 개를 심은 엔진을 만든다."""
    engine = create_engine("sqlite://", future=True)
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))
        pair_id = 0
        for job_id in jobs:
            conn.execute(
                text(
                    "INSERT INTO ocr_jobs (id, status, image_path, result_json, "
                    "curation_reviewed) VALUES (:id, 'done', :img, '{}', 1)"
                ),
                {"id": job_id, "img": f"/data/up/{job_id}.jpeg"},
            )
            for row_index in range(pairs_per_job):
                pair_id += 1
                conn.execute(
                    text(
                        "INSERT INTO training_pairs (id, job_id, crop_ref, row_index, supply, "
                        "draft_supply, status, exclusion_reason, reviewed_at) VALUES "
                        "(:pid, :jid, :ref, :ri, :sup, :sup, 'included', NULL, NULL)"
                    ),
                    {
                        "pid": pair_id,
                        "jid": job_id,
                        "ref": f"job-{job_id}/row-{row_index}",
                        "ri": row_index,
                        "sup": 3000 if row_index == 0 else 5000,
                    },
                )
    return engine


def _infer_ok(result=RESULT):
    """크롭을 실제로 쓰는 Fake 추론 — 받은 디렉터리와 호출 이력을 남긴다."""
    seen = []

    def infer_fn(image_path, crop_dir, job_id):
        seen.append({"image_path": image_path, "crop_dir": Path(crop_dir), "job_id": job_id})
        Path(crop_dir).mkdir(parents=True, exist_ok=True)
        (Path(crop_dir) / "row-0.png").write_bytes(b"new")
        return result

    return infer_fn, seen


# ---------------------------------------------------------------------------
# forecast_job — 예측 전용 재추론
# ---------------------------------------------------------------------------


def test_forecast_counts_rows_pairs_relinks_and_orphans():
    """새 rows(2개)가 못 짝짓는 옛 쌍(supply 9999)을 하나 심어 orphan 반쪽도 실측한다."""
    engine = _engine(jobs=(27,), pairs_per_job=2)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO training_pairs (id, job_id, crop_ref, row_index, supply, "
                "draft_supply, status, exclusion_reason, reviewed_at) VALUES "
                "(3, 27, 'job-27/row-2', 2, 9999, 9999, 'included', NULL, NULL)"
            )
        )
    queue = WorkerQueue(engine)
    infer_fn, seen = _infer_ok()

    forecast = forecast_job(queue, infer_fn, 27)

    assert forecast == JobForecast(job_id=27, new_row_count=2, pair_count=3, relinked=2, orphaned=1)
    assert seen[0]["image_path"] == "/data/up/27.jpeg"


def test_forecast_hands_inference_a_directory_that_is_gone_afterwards():
    """크롭 루트 밖의 임시 디렉터리라 job-N.tmp/job-N.old가 만들어질 자리 자체가 없다(spec §4)."""
    queue = WorkerQueue(_engine())
    infer_fn, seen = _infer_ok()

    forecast_job(queue, infer_fn, 27)

    crop_dir = seen[0]["crop_dir"]
    assert not crop_dir.exists(), "TemporaryDirectory 종료 시 삭제된다"


def test_forecast_of_a_missing_job_is_an_error_not_a_crash():
    forecast = forecast_job(WorkerQueue(_engine()), _infer_ok()[0], 999)
    assert forecast.error is not None
    assert forecast.pair_count == 0


def test_an_ordinary_failure_becomes_an_error_forecast_with_the_pairs_filled_in():
    """fetch_pairs는 추론과 무관하게 성립한다 — 분모에서 빠지는 규모를 보여야 한다(spec §6)."""

    def boom(*_a):
        raise RuntimeError("warp 실패")

    forecast = forecast_job(WorkerQueue(_engine(jobs=(51,), pairs_per_job=3)), boom, 51)

    assert forecast.pair_count == 3
    assert forecast.new_row_count == 0
    assert "warp 실패" in forecast.error


def test_a_degenerate_collapse_is_not_swallowed_as_a_job_error():
    """붕괴는 프로세스 지속 상태라 다음 잡의 예측을 조용히 오염시킨다 — 그대로 올라온다(spec §5)."""

    def spam(*_a):
        raise DegenerateOutputError("!" * 32)

    with pytest.raises(DegenerateOutputError):
        forecast_job(WorkerQueue(_engine()), spam, 27)


# ---------------------------------------------------------------------------
# main — 재개·귀속·붕괴 수렴
# ---------------------------------------------------------------------------


def _wire(monkeypatch, engine, infer_fn, code_version="sha-1"):
    """운영 배선(DB·모델)을 테스트 대역으로 갈아끼운다."""
    monkeypatch.setattr(rd, "build_queue", lambda: WorkerQueue(engine))
    monkeypatch.setattr(rd, "build_infer_fn", lambda: infer_fn)
    monkeypatch.setattr(rd, "code_version", lambda: code_version)


def _snapshot(engine):
    with engine.begin() as conn:
        jobs = conn.execute(
            text("SELECT id, status, result_json, curation_reviewed FROM ocr_jobs ORDER BY id")
        ).fetchall()
        pairs = conn.execute(
            text(
                "SELECT id, crop_ref, row_index, status, exclusion_reason, reviewed_at "
                "FROM training_pairs ORDER BY id"
            )
        ).fetchall()
    return [tuple(r) for r in jobs], [tuple(r) for r in pairs]


def test_the_dryrun_leaves_every_job_and_pair_row_untouched(tmp_path, monkeypatch):
    """AC — 드라이런 전후 ocr_jobs·training_pairs 전 행이 같다(spec §4)."""
    engine = _engine(jobs=(27, 40), pairs_per_job=2)
    infer_fn, _ = _infer_ok()
    _wire(monkeypatch, engine, infer_fn)
    before = _snapshot(engine)

    rd.main(["--job", "27", "40", "--out", str(tmp_path / "forecast.jsonl")])

    assert _snapshot(engine) == before


def test_the_dryrun_never_creates_swap_markers_in_the_crop_root(tmp_path, monkeypatch):
    """AC — job-*.tmp/job-*.old가 0개이고 기존 job-N/ 내용이 불변(런북 5단계 판정 보호).

    임시 디렉터리까지 tmp_path 안으로 들여야 이 단언이 실제 관찰이 된다 — 드라이런은
    crops_root를 인자로도 env로도 받지 않으므로 그냥 두면 어떤 구현에서도 통과한다.
    setenv("TMPDIR")로는 부족하다: gettempdir()이 첫 호출 결과를 모듈 전역에 캐시해
    세션 도중의 env 변경이 반영되지 않는다(실측).
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    crops_root = tmp_path / "ocr_crops"
    (crops_root / "job-27").mkdir(parents=True)
    (crops_root / "job-27" / "row-0.png").write_bytes(b"old")
    infer_fn, _ = _infer_ok()
    _wire(monkeypatch, _engine(jobs=(27,)), infer_fn)

    rd.main(["--job", "27", "--out", str(tmp_path / "forecast.jsonl")])

    assert sorted(p.name for p in crops_root.iterdir()) == ["job-27"]
    assert (crops_root / "job-27" / "row-0.png").read_bytes() == b"old"
    assert list(tmp_path.rglob("job-*.tmp")) == []
    assert list(tmp_path.rglob("job-*.old")) == []


def test_a_resumed_run_skips_jobs_already_in_the_out_file(tmp_path, monkeypatch):
    out = tmp_path / "forecast.jsonl"
    out.write_text(
        meta_line(RunMeta(job_ids=(27, 40), code_version="sha-1"))
        + "\n"
        + record_line(_fc(27, rows=2, pairs=2, relinked=2, orphaned=0))
        + "\n",
        encoding="utf-8",
    )
    infer_fn, seen = _infer_ok()
    _wire(monkeypatch, _engine(jobs=(27, 40)), infer_fn)

    rd.main(["--job", "27", "40", "--out", str(out)])

    assert [s["job_id"] for s in seen] == [40], "이미 예측한 잡은 다시 추론하지 않는다"
    assert sorted(parse_done(out.read_text(encoding="utf-8").splitlines())) == [27, 40]


def test_a_torn_last_line_in_the_out_file_is_refused_not_a_traceback(tmp_path, monkeypatch):
    """kill -9·ENOSPC로 --out 마지막 줄이 잘려도 traceback이 아니라 EXIT_USAGE로 거부한다."""
    out = tmp_path / "forecast.jsonl"
    out.write_text(
        meta_line(RunMeta(job_ids=(27, 40), code_version="sha-1"))
        + "\n"
        + '{"job_id": 40, "new_ro'
        + "\n",
        encoding="utf-8",
    )
    infer_fn, seen = _infer_ok()
    _wire(monkeypatch, _engine(jobs=(27, 40)), infer_fn)

    with pytest.raises(SystemExit) as exc:
        rd.main(["--job", "27", "40", "--out", str(out)])
    assert exc.value.code == rd.EXIT_USAGE
    assert seen == []


def test_a_resume_with_a_different_batch_or_code_is_refused(tmp_path, monkeypatch):
    """과거 예측이 이번 합계에 섞이지 않게 거부하고 비0으로 종료한다(spec §3.4)."""
    out = tmp_path / "forecast.jsonl"
    original = (
        meta_line(RunMeta(job_ids=(27, 40), code_version="sha-1"))
        + "\n"
        + record_line(_fc(27))
        + "\n"
    )
    out.write_text(original, encoding="utf-8")
    infer_fn, seen = _infer_ok()
    _wire(monkeypatch, _engine(jobs=(27, 40, 51)), infer_fn)

    with pytest.raises(SystemExit) as exc:
        rd.main(["--job", "27", "40", "51", "--out", str(out)])
    assert exc.value.code == rd.EXIT_USAGE
    assert out.read_text(encoding="utf-8") == original, "거부한 실행은 out을 건드리지 않는다"
    assert seen == []

    # 코드 SHA 축도 같은 판정이다 — 드라이런과 3단계 사이에 배포가 지나간 경우.
    _wire(monkeypatch, _engine(jobs=(27, 40)), infer_fn, code_version="sha-2")
    with pytest.raises(SystemExit) as exc:
        rd.main(["--job", "27", "40", "--out", str(out)])
    assert exc.value.code == rd.EXIT_USAGE


def test_a_run_without_a_code_version_is_refused_instead_of_failing_open(tmp_path, monkeypatch):
    """두 unknown 상태를 같다고 보면 배포를 사이에 둔 재개가 조용히 통과한다(spec §3.4 취지)."""
    infer_fn, seen = _infer_ok()
    _wire(monkeypatch, _engine(jobs=(27,)), infer_fn, code_version=None)
    out = tmp_path / "forecast.jsonl"

    with pytest.raises(SystemExit) as exc:
        rd.main(["--job", "27", "--out", str(out)])

    assert exc.value.code == rd.EXIT_USAGE
    assert not out.exists(), "거부한 실행은 재개 파일을 만들지 않는다"
    assert seen == []


def test_the_summary_only_counts_records_from_this_batch(tmp_path, monkeypatch, capsys):
    out = tmp_path / "forecast.jsonl"
    out.write_text(
        meta_line(RunMeta(job_ids=(27,), code_version="sha-1"))
        + "\n"
        + record_line(_fc(27, rows=2, pairs=2, relinked=2, orphaned=0))
        + "\n"
        + record_line(_fc(999, rows=50, pairs=50, relinked=0, orphaned=50))
        + "\n",
        encoding="utf-8",
    )
    infer_fn, _ = _infer_ok()
    _wire(monkeypatch, _engine(jobs=(27,)), infer_fn)

    rd.main(["--job", "27", "--out", str(out)])

    printed = capsys.readouterr().out
    assert "(잡 1건)" in printed, "RunMeta.job_ids 밖의 레코드는 집계에 섞이지 않는다"
    assert "999" not in printed


def test_an_ordinary_failure_isolates_the_job_and_keeps_forecasting(tmp_path, monkeypatch):
    """warp 실패·파일 부재는 다음 잡의 예측을 오염시키지 않는다(spec §5)."""
    ok_fn, seen = _infer_ok()

    def infer_fn(image_path, crop_dir, job_id):
        if job_id == 27:
            raise RuntimeError("warp 실패")
        return ok_fn(image_path, crop_dir, job_id)

    out = tmp_path / "forecast.jsonl"
    _wire(monkeypatch, _engine(jobs=(27, 40)), infer_fn)

    rd.main(["--job", "27", "40", "--out", str(out)])

    done = parse_done(out.read_text(encoding="utf-8").splitlines())
    assert "warp 실패" in done[27].error
    assert done[40].error is None


def test_the_shell_retry_loop_converges_on_a_job_that_always_collapses(tmp_path, monkeypatch):
    """크래시루프 수렴 — 잡 하나가 소비하는 재시도는 최대 1회다(spec §5).

    워커의 은퇴 갈래(worker/poll.py의 qwen_jobs_before == 0)를 그대로 가져온 규칙이다.
    셸 `until` 루프가 끝난다는 것이 이 테스트의 계약이다.
    """
    ok_fn, _ = _infer_ok()

    def infer_fn(image_path, crop_dir, job_id):
        if job_id == 40:
            raise DegenerateOutputError("!" * 32)
        return ok_fn(image_path, crop_dir, job_id)

    out = tmp_path / "forecast.jsonl"
    _wire(monkeypatch, _engine(jobs=(27, 40, 51)), infer_fn)
    argv = ["--job", "27", "40", "51", "--out", str(out)]

    # ① 1회차 — 앞선 잡 27이 Qwen을 불렀으므로 기록 없이 비0 종료(다음 실행이 깨끗한
    #    프로세스에서 다시 시도한다).
    with pytest.raises(SystemExit) as exc:
        rd.main(argv)
    assert exc.value.code == rd.EXIT_DEGENERATE
    assert sorted(parse_done(out.read_text(encoding="utf-8").splitlines())) == [27]

    # ② 2회차 — 27을 건너뛰어 40이 이 프로세스의 첫 Qwen 잡이 된다 → error로 확정 기록.
    with pytest.raises(SystemExit) as exc:
        rd.main(argv)
    assert exc.value.code == rd.EXIT_DEGENERATE
    done = parse_done(out.read_text(encoding="utf-8").splitlines())
    assert done[40].error == rd.DEGENERATE
    assert done[40].pair_count == 2, "예측 불가 잡의 pairs는 조회해서 채운다"

    # ③ 3회차 — 40을 건너뛰고 나머지를 완주해 0으로 종료(SystemExit 없음).
    rd.main(argv)
    assert sorted(parse_done(out.read_text(encoding="utf-8").splitlines())) == [27, 40, 51]


def test_a_jobs_file_is_read_one_id_per_line(tmp_path, monkeypatch):
    jobs_file = tmp_path / "jobs.txt"
    jobs_file.write_text("27\n\n40\n", encoding="utf-8")
    infer_fn, seen = _infer_ok()
    _wire(monkeypatch, _engine(jobs=(27, 40)), infer_fn)

    rd.main(["--jobs-file", str(jobs_file), "--out", str(tmp_path / "forecast.jsonl")])

    assert sorted(s["job_id"] for s in seen) == [27, 40]


def test_a_malformed_jobs_file_exits_with_the_non_retryable_code(tmp_path, monkeypatch):
    """셸 until 루프는 코드를 구분하지 않는다 — 사람이 고쳐야 하는 실패는 1이 아니어야 한다.

    "--5"·"²"는 lstrip("-").isdigit()는 통과하지만 int()에서 ValueError를 내던 값이다
    (leading dash 전량 제거 + Unicode 숫자 오탐) — 같은 EXIT_USAGE 경로로 들어와야 한다.
    """
    infer_fn, _ = _infer_ok()
    _wire(monkeypatch, _engine(jobs=(27,)), infer_fn)

    for i, line in enumerate(("id", "--5", "²")):
        jobs_file = tmp_path / f"jobs{i}.txt"
        jobs_file.write_text(f"{line}\n27\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc:
            rd.main(["--jobs-file", str(jobs_file), "--out", str(tmp_path / f"forecast{i}.jsonl")])
        assert exc.value.code == rd.EXIT_USAGE


def test_an_unhandled_exception_does_not_look_like_a_retryable_collapse(tmp_path):
    """미처리 예외(exit 1)와 붕괴 코드가 겹치면 런북 until 루프가 무한 재시도한다.

    DB env 누락은 build_engine의 os.environ["DB_NAME"]에서 KeyError로 죽는 실재 경로다
    (야간 무인 실행에서 실제로 밟히는 갈래). 전제: 이 체크아웃이 git 저장소라
    code_version()이 성립한다 — 아니면 EXIT_USAGE(2)가 먼저 난다.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("DB_")}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.reprocess_dryrun",
            "--job",
            "1",
            "--out",
            str(tmp_path / "f.jsonl"),
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 1, proc.stderr.decode(errors="replace")
    assert rd.EXIT_DEGENERATE != 1, "재시도 가능한 코드가 1이면 이 실패가 무한 재시도된다"


def test_the_dryrun_module_keeps_model_and_db_imports_out_of_module_scope():
    """모듈 상단 규약 — 모델·DB 의존은 함수 안에서만 import한다(worker/main.py와 동일).

    규약이 docstring에만 있으면 지연 import 회귀가 CI(worker·cv extra 설치됨)에서
    초록으로 통과한다 — Task 1이 worker/plan.py에 건 가드와 같은 수법으로 소스를 본다.
    """
    src = Path(__file__).resolve().parents[1] / "tools" / "reprocess_dryrun.py"
    module_level, in_functions = import_scopes(src)

    forbidden = {
        "worker.main",
        "worker.db",
        "handwriting.infer_job",
        "handwriting.infer_photo",
        "torch",
        "mlx",
        "cv2",
        "numpy",
        "sqlalchemy",
    }
    assert module_level & forbidden == set()
    assert {"worker.main", "worker.db", "handwriting.infer_job"} <= in_functions


def test_build_infer_fn_passes_models_in_the_slot_infer_job_expects(monkeypatch):
    """배선은 worker/main.py와 같다 — infer_job(image_path, models, crop_dir, job_id).

    Fake 모듈 주입은 tests/test_worker_models.py 선례를 따른다(실모델·torch 비의존).
    """
    calls = []
    fake = types.ModuleType("handwriting.infer_job")
    fake.infer_job = lambda *args: calls.append(args) or {"rows": []}
    monkeypatch.setitem(sys.modules, "handwriting.infer_job", fake)
    monkeypatch.setattr("worker.main.load_models", lambda: "MODELS")

    rd.build_infer_fn()("/data/up/27.jpeg", "/tmp/crops", 27)

    assert calls == [("/data/up/27.jpeg", "MODELS", "/tmp/crops", 27)]
