"""tools.reprocess_dryrun — 무커밋 재처리 드라이런(Issue #100).

순수 계층(집계·렌더·jsonl)은 여기서 직접 단위테스트하고, 글루(forecast_job·main)는
인메모리 sqlite 엔진 + Fake 추론으로 모델 없이 돈다(tests/test_worker_db.py 관례).
"""

from tools.reprocess_dryrun import (
    BatchSummary,
    JobForecast,
    RunMeta,
    meta_line,
    parse_done,
    parse_meta,
    record_line,
    render,
    summarize,
)


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
