"""tools.reprocess_dryrun — 무커밋 재처리 드라이런(Issue #100).

순수 계층(집계·렌더·jsonl)은 여기서 직접 단위테스트하고, 글루(forecast_job·main)는
인메모리 sqlite 엔진 + Fake 추론으로 모델 없이 돈다(tests/test_worker_db.py 관례).
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from handwriting.amount_read import DegenerateOutputError
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
    queue = WorkerQueue(_engine(jobs=(27,), pairs_per_job=2))
    infer_fn, seen = _infer_ok()

    forecast = forecast_job(queue, infer_fn, 27)

    assert forecast == JobForecast(job_id=27, new_row_count=2, pair_count=2, relinked=2, orphaned=0)
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
