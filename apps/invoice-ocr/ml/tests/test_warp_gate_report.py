"""tools.warp_gate_report 순수 계층 단위테스트(ssh/cv2 비의존, 합성 데이터만)."""

import json

import pytest

from tools.warp_gate_report import main, parse_job_rows_tsv, parse_warp_pairs_tsv

# --- TSV 파싱 ---


def test_parse_job_rows_tsv_maps_boolean_strings():
    # SQL이 result_json->>'$.warp_ok'로 값 하나만 뽑으므로 로컬 JSON 파싱이 없다.
    text = "id\tresult_json->>'$.warp_ok'\timage_path\n1\ttrue\t/data/ocr_uploads/a.jpg\n2\tfalse\t/data/ocr_uploads/b.jpg\n"
    assert parse_job_rows_tsv(text) == [
        {"job_id": 1, "warp_ok": True, "image_path": "/data/ocr_uploads/a.jpg"},
        {"job_id": 2, "warp_ok": False, "image_path": "/data/ocr_uploads/b.jpg"},
    ]


def test_parse_job_rows_tsv_treats_null_as_unknown():
    # result_json이 NULL이거나 warp_ok 키가 없는 잡(미처리·failed) — 둘 다 SQL이 NULL을 준다.
    text = "id\tresult_json->>'$.warp_ok'\timage_path\n3\tNULL\t/data/ocr_uploads/c.jpg\n4\t\t/data/ocr_uploads/d.jpg\n"
    assert parse_job_rows_tsv(text) == [
        {"job_id": 3, "warp_ok": None, "image_path": "/data/ocr_uploads/c.jpg"},
        {"job_id": 4, "warp_ok": None, "image_path": "/data/ocr_uploads/d.jpg"},
    ]


def test_parse_job_rows_tsv_rejects_unexpected_value():
    # 예상 밖 표현을 None으로 흡수하면 무회귀 분모가 조용히 줄어 캘리브 결론이 왜곡된다.
    with pytest.raises(ValueError, match="warp_ok"):
        parse_job_rows_tsv(
            "id\tresult_json->>'$.warp_ok'\timage_path\n7\t{\"rows\": []}\t/data/ocr_uploads/e.jpg\n"
        )


def test_parse_job_rows_tsv_keeps_image_path():
    # 재워프 주 기준(spec §4.1)이 잡 → 원본 사진 매핑을 요구한다.
    text = "id\twarp_ok\timage_path\n1\ttrue\t/data/ocr_uploads/ab12.jpg\n"
    assert parse_job_rows_tsv(text) == [
        {"job_id": 1, "warp_ok": True, "image_path": "/data/ocr_uploads/ab12.jpg"}
    ]


def test_parse_job_rows_tsv_treats_null_image_path_as_missing():
    # ocr_jobs.image_path는 nullable이다(db/migration_007_ml_seam.sql:30).
    text = "id\twarp_ok\timage_path\n2\ttrue\tNULL\n"
    assert parse_job_rows_tsv(text) == [{"job_id": 2, "warp_ok": True, "image_path": None}]


def test_parse_job_rows_tsv_treats_empty_image_path_as_missing():
    # 빈 image_path도 missing으로 합류한다 — warp_ok의 ""→None 대칭(WARP_OK_VALUES[""]).
    # 그렇지 않으면 빈 경로가 "사진 없음"이 아니라 "사진 있음(경로 빈값)"으로 오분류된다.
    text = "id\twarp_ok\timage_path\n5\ttrue\t\n6\tfalse\t/data/ocr_uploads/f.jpg\n"
    assert parse_job_rows_tsv(text) == [
        {"job_id": 5, "warp_ok": True, "image_path": None},
        {"job_id": 6, "warp_ok": False, "image_path": "/data/ocr_uploads/f.jpg"},
    ]


def test_parse_job_rows_tsv_rejects_row_with_wrong_column_count():
    # 열이 하나라도 어긋나면 매핑이 조용히 밀린다 — 캘리브 근거가 통째로 왜곡되므로 fail-fast.
    with pytest.raises(ValueError, match="열"):
        parse_job_rows_tsv("id\twarp_ok\timage_path\n3\ttrue\n")


def test_parse_job_rows_tsv_rejects_image_path_with_embedded_tab():
    # image_path 안의 탭이 열 경계를 밀어 4열이 되는 경우 — 자매 파서
    # curation_enrich.parse_jobs_tsv:79가 지적하는 동일 위험(업로드 파일명 suffix의 탭 혼입)에
    # 대한 방어선이다. 조용히 밀리지 않고 fail-fast해야 한다.
    text = "id\twarp_ok\timage_path\n6\ttrue\t/data/ocr_uploads/a\tb.jpg\n"
    with pytest.raises(ValueError, match="열"):
        parse_job_rows_tsv(text)


def test_parse_warp_pairs_tsv_keeps_only_the_columns_pair_rows_uses():
    # pair_rows(warp_gate_calib)가 쓰는 열은 job_id/row_index/status뿐이다(#63로 갈라진
    # curation_enrich.parse_pairs_tsv의 11열과 달리 이 파서는 그 3열만 안다).
    text = "job_id\trow_index\tstatus\n1\t0\tincluded\n1\t1\texcluded\n"
    assert parse_warp_pairs_tsv(text) == [
        {"job_id": 1, "row_index": 0, "status": "included"},
        {"job_id": 1, "row_index": 1, "status": "excluded"},
    ]


def test_parse_warp_pairs_tsv_rejects_row_with_wrong_column_count():
    # 자매 파서 parse_job_rows_tsv와 같은 계약 — 열이 조용히 밀리면 이 커밋의 임계 확정
    # 근거인 pairs 축 전체가 왜곡되므로 fail-fast해야 한다.
    with pytest.raises(ValueError):
        parse_warp_pairs_tsv("job_id\trow_index\tstatus\n1\t0\n")


# --- CLI ---


def test_report_without_cache_exits_with_fetch_guidance(tmp_path):
    # fetch 전에 report를 돌리면 맨 FileNotFoundError 대신 다음 행동을 지시해야 한다.
    with pytest.raises(SystemExit) as excinfo:
        main(["--cache", str(tmp_path), "report"])
    assert "fetch" in str(excinfo.value)


# --- 재워프 기준 평가·리포트(Task 6) ---


def _seed_cache(tmp_path, *, jobs=None):
    """`evaluate_rewarped`/`report` CLI 테스트용 최소 캐시 시드 — jobs.json+meta.json+pairs.json.

    fetch가 만드는 캐시 레이아웃 중 report가 참조하는 최소 부분만 합성한다 — 원본
    사진(uploads/)·warped.png는 개별 테스트가 필요할 때 따로 만든다.
    """
    if jobs is None:
        jobs = [{"job_id": 1, "warp_ok": True, "image_path": None}]
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "jobs.json").write_text(json.dumps(jobs))
    (tmp_path / "meta.json").write_text(json.dumps({"host": "h", "fetched_at": "t"}))
    (tmp_path / "pairs.json").write_text(json.dumps([]))
    return tmp_path


def test_evaluate_rewarped_marks_jobs_without_an_image_path(tmp_path):
    # ocr_jobs.image_path는 nullable이다 — 조인이 조용히 밀리면 전 잡이 정상처럼 보인다.
    from tools import warp_gate_report as wgr

    _seed_cache(tmp_path, jobs=[{"job_id": 1, "warp_ok": True, "image_path": None}])
    recs = wgr.evaluate_rewarped(tmp_path, labels={})
    assert recs[0]["status"] == wgr.STATUS_UPLOAD_MISSING
    assert recs[0]["metrics"] is None


def test_evaluate_rewarped_resolves_uploads_by_basename(tmp_path, monkeypatch):
    # jobs.json의 image_path는 macmini 절대경로다 — 로컬 캐시에는 basename으로만 존재한다.
    from tools import warp_gate_report as wgr

    _seed_cache(
        tmp_path,
        jobs=[{"job_id": 1, "warp_ok": True, "image_path": "/remote/data/ocr_uploads/ab12.jpg"}],
    )
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    (tmp_path / "uploads" / "ab12.jpg").write_bytes(b"x")
    seen = []
    monkeypatch.setattr(wgr, "rewarp_job", lambda p, **kw: (seen.append(p), ("ok", object()))[1])
    monkeypatch.setattr(wgr, "job_metrics", lambda w: {"std": {}, "enh": {}})
    wgr.evaluate_rewarped(tmp_path, labels={})
    assert seen[0].name == "ab12.jpg"
    assert seen[0].parent == tmp_path / "uploads"


def test_evaluate_rewarped_demotes_path_traversal_image_path_without_raising(tmp_path):
    # image_path는 DB VARCHAR(512) 자유형이라 신뢰할 수 없다 — basename이 '..'/'.'로
    # 붕괴하는 입력은 uploads/ 밖을 가리킬 수 있다(M3). 예외로 전수 리포트를 죽이면 앞선
    # fetch 비용(원본 사진 171MB tar)이 날아간다 — 그 잡 하나만 분모 밖으로 강등한다
    # (warp_gate_rows.rewarp_job의 "예외를 던지지 않는다" 계약과 동일).
    from tools import warp_gate_report as wgr

    _seed_cache(tmp_path, jobs=[{"job_id": 1, "warp_ok": True, "image_path": "/a/b/.."}])
    recs = wgr.evaluate_rewarped(tmp_path, labels={})
    assert recs[0]["status"] == wgr.STATUS_INVALID_IMAGE_PATH
    assert recs[0]["metrics"] is None
    assert recs[0]["stored_metrics"] is None


def test_evaluate_rewarped_fills_stored_metrics_only_when_the_warped_png_exists(
    tmp_path, monkeypatch
):
    # 저장 워프 대조표(spec §4.1 참고 축)의 입력이다 — 없으면 None이어야 '차이 없음'과 구분된다.
    from tools import warp_gate_report as wgr

    _seed_cache(
        tmp_path, jobs=[{"job_id": 1, "warp_ok": True, "image_path": "/r/ocr_uploads/a.jpg"}]
    )
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    (tmp_path / "uploads" / "a.jpg").write_bytes(b"x")
    monkeypatch.setattr(wgr, "rewarp_job", lambda p, **kw: ("ok", object()))
    monkeypatch.setattr(wgr, "job_metrics", lambda w: {"std": {}, "enh": {}})
    assert wgr.evaluate_rewarped(tmp_path, labels={})[0]["stored_metrics"] is None


def test_evaluate_rewarped_fills_stored_metrics_and_feeds_drift_when_warped_png_is_readable(
    tmp_path, monkeypatch
):
    # _stored_metrics가 항상 None을 돌려줘도 위 테스트(warped.png 없음)는 못 잡는다(H4) — 이
    # 경로가 죽으면 #18 승계 라벨 무효 잡의 육안 편입(spec §4.1)이 조용히 0건이 된다. 여기서는
    # warped.png를 실제로 만들고 compute_metrics를 주입해 stored_metrics가 채워지는 경로와
    # 그 값이 stored_vs_rewarp의 drift 행까지 흘러가는 경로를 함께 고정한다.
    cv2 = pytest.importorskip("cv2", exc_type=ImportError)
    from handwriting.warp_gate import WarpGateMetrics
    from tools import warp_gate_calib
    from tools import warp_gate_report as wgr

    _seed_cache(
        tmp_path, jobs=[{"job_id": 1, "warp_ok": True, "image_path": "/r/ocr_uploads/a.jpg"}]
    )
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    (tmp_path / "uploads" / "a.jpg").write_bytes(b"x")
    warped_dir = tmp_path / "warped" / "job-1"
    warped_dir.mkdir(parents=True)
    (warped_dir / "warped.png").write_bytes(b"fake-png")

    stored = WarpGateMetrics(
        hline_count=6, pitch_dev=0.30, blue_ratio_left=0.0, blue_ratio_right=0.0
    )
    monkeypatch.setattr(cv2, "imread", lambda path: object())
    monkeypatch.setattr("handwriting.warp_gate.compute_metrics", lambda img: stored)
    monkeypatch.setattr(wgr, "rewarp_job", lambda p, **kw: ("ok", object()))
    monkeypatch.setattr(
        wgr,
        "job_metrics",
        lambda w: {
            "std": {
                "hline_count": 17,
                "pitch_dev": 0.02,
                "blue_ratio_left": 0.2,
                "blue_ratio_right": 0.2,
            },
            "enh": {
                "hline_count": 20,
                "pitch_dev": 0.03,
                "blue_ratio_left": 0.3,
                "blue_ratio_right": 0.3,
            },
        },
    )

    recs = wgr.evaluate_rewarped(tmp_path, labels={})
    assert recs[0]["stored_metrics"] == {
        "hline_count": 6,
        "pitch_dev": 0.30,
        "blue_ratio_left": 0.0,
        "blue_ratio_right": 0.0,
    }
    drift = warp_gate_calib.stored_vs_rewarp(recs)
    assert drift and drift[0]["job_id"] == 1
    assert drift[0]["hline_count"] == {"rewarp": 17, "stored": 6}


def test_evaluate_rewarped_wires_suspect_and_unlabeled_sets_to_record_labels(tmp_path):
    # labels의 suspects/unlabeled 키가 서로 바뀌면(H3) 파손군·미라벨군이 뒤집혀 마진표가
    # 통째로 반전되는데 기존 evaluate_rewarped 테스트는 전부 labels={}였다.
    from tools import warp_gate_report as wgr

    _seed_cache(
        tmp_path,
        jobs=[
            {"job_id": 1, "warp_ok": True, "image_path": None},
            {"job_id": 2, "warp_ok": True, "image_path": None},
            {"job_id": 3, "warp_ok": True, "image_path": None},
        ],
    )
    recs = wgr.evaluate_rewarped(tmp_path, labels={"suspects": {2}, "unlabeled": {3}})
    labels = {r["job_id"]: r["label"] for r in recs}
    assert labels == {1: "normal", 2: "suspect", 3: "unlabeled"}


def test_evaluate_rewarped_leaves_metrics_none_when_rewarp_fails(tmp_path, monkeypatch):
    # `metrics = job_metrics(warped) if status == STATUS_OK else None`가 무조건 채워져도
    # 기존 테스트가 못 잡는다(M4) — 재워프 실패 잡의 record 형태를 고정한다.
    from tools import warp_gate_report as wgr

    _seed_cache(
        tmp_path, jobs=[{"job_id": 1, "warp_ok": True, "image_path": "/r/ocr_uploads/a.jpg"}]
    )
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    (tmp_path / "uploads" / "a.jpg").write_bytes(b"x")
    monkeypatch.setattr(wgr, "rewarp_job", lambda p, **kw: ("quad_missing", None))
    recs = wgr.evaluate_rewarped(tmp_path, labels={})
    assert recs[0]["status"] == "quad_missing"
    assert recs[0]["metrics"] is None


def test_report_writes_machine_readable_metrics_next_to_the_markdown(tmp_path, monkeypatch):
    # Phase 1의 임계 도출은 62잡 × 8지표를 손으로 읽는 것이 아니라 이 JSON을 계산해서 한다.
    from tools import warp_gate_report as wgr

    _seed_cache(tmp_path)  # jobs.json + meta.json + pairs.json 최소 시드(파일 상단 헬퍼)
    monkeypatch.setattr(wgr, "evaluate_rewarped", lambda cache, labels: [])
    wgr.main(["--cache", str(tmp_path), "report", "--suspect", "24", "--unlabeled", "5"])

    assert (tmp_path / "warp_gate_report.md").exists()
    assert json.loads((tmp_path / "warp_gate_metrics.json").read_text()) == []


def test_report_excludes_jobs_above_max_job_id_from_metrics(tmp_path, monkeypatch):
    # 캘리브 도중 생긴 신규 잡이 검증 없이 정상군 최악값을 움직이면 임계 근거가 오염된다.
    from tools import warp_gate_report as wgr

    _seed_cache(tmp_path)
    std = {"hline_count": 17, "pitch_dev": 0.02, "blue_ratio_left": 0.2, "blue_ratio_right": 0.2}
    enh = {"hline_count": 20, "pitch_dev": 0.03, "blue_ratio_left": 0.3, "blue_ratio_right": 0.3}
    monkeypatch.setattr(
        wgr,
        "evaluate_rewarped",
        lambda cache, labels: [
            {
                "job_id": 50,
                "label": "normal",
                "prev_warp_ok": True,
                "status": "ok",
                "metrics": {"std": std, "enh": enh},
                "stored_metrics": None,
            },
            {
                "job_id": 63,  # 상한과 정확히 같은 id — 포함(<=)돼야 한다
                "label": "normal",
                "prev_warp_ok": True,
                "status": "ok",
                "metrics": {"std": std, "enh": enh},
                "stored_metrics": None,
            },
            {
                "job_id": 64,
                "label": "normal",
                "prev_warp_ok": True,
                "status": "ok",
                "metrics": {"std": std, "enh": enh},
                "stored_metrics": None,
            },
        ],
    )
    wgr.main(["--cache", str(tmp_path), "report", "--max-job-id", "63"])
    metrics = json.loads((tmp_path / "warp_gate_metrics.json").read_text())
    assert [m["job_id"] for m in metrics] == [50, 63]


def test_report_cli_wires_suspect_and_unlabeled_args_into_labels(tmp_path, monkeypatch):
    # CLI → evaluate_rewarped 배선이 무단언이었다(H3) — --suspect/--unlabeled가 정확히
    # labels dict에 실리는지 인자 캡처로 확인한다.
    from tools import warp_gate_report as wgr

    _seed_cache(tmp_path)
    seen = {}

    def fake_evaluate(cache, labels):
        seen["labels"] = labels
        return []

    monkeypatch.setattr(wgr, "evaluate_rewarped", fake_evaluate)
    wgr.main(["--cache", str(tmp_path), "report", "--suspect", "24", "38", "--unlabeled", "2", "3"])
    assert seen["labels"] == {"suspects": {24, 38}, "unlabeled": {2, 3}}


# --- 원격 루트 파라미터화 + 원본 사진·학습쌍 동기화 (Task 3) ---


def _empty_tar() -> bytes:
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w"):
        pass
    return buf.getvalue()


def test_sync_remote_files_reads_from_the_requested_remote_root(tmp_path, monkeypatch):
    # 원본 사진은 ocr_crops가 아니라 ocr_uploads에 있다 — 루트가 고정이면 주 기준(재워프)이
    # 아예 성립하지 않는다.
    from tools import cache_sync

    seen = []

    def fake_run_ssh(host, script, **kw):
        seen.append(script)
        return b"a.jpg\n" if "ls -d" in script else _empty_tar()

    monkeypatch.setattr(cache_sync, "run_ssh", fake_run_ssh)
    cache_sync.sync_remote_files(
        "h", "/e", pattern="*", dest=tmp_path / "uploads", root="ocr_uploads"
    )

    assert any("ocr_uploads" in s for s in seen)
    assert not any("ocr_crops" in s for s in seen)


def test_sync_remote_files_defaults_to_the_crops_root(tmp_path, monkeypatch):
    # 기존 호출자(blank_crop_report·기존 warped 동기화)의 동작은 무변경이어야 한다.
    from tools import cache_sync

    seen = []
    monkeypatch.setattr(
        cache_sync, "run_ssh", lambda host, script, **kw: seen.append(script) or b""
    )
    cache_sync.remote_file_list("h", "/e", "job-*/warped.png")

    assert "ocr_crops" in seen[0]


def test_sync_remote_files_forwards_the_timeout_to_run_ssh(tmp_path, monkeypatch):
    # 171MB tar가 run_ssh 기본 600초를 넘기면 Phase 0 전체가 막힌다(R6).
    from tools import cache_sync

    seen = []
    monkeypatch.setattr(
        cache_sync,
        "run_ssh",
        lambda host, script, **kw: seen.append(kw.get("timeout")) or b"",
    )
    cache_sync.remote_file_list("h", "/e", "*", root="ocr_uploads", timeout=3600.0)
    assert seen == [3600.0]


def test_fetch_all_syncs_uploads_and_pairs_alongside_warped(tmp_path, monkeypatch):
    # 주 기준(원본 재워프)과 축 ②-a 모집단(학습쌍)이 한 fetch로 갖춰져야 한다 —
    # 두 번 나뉘면 캐시 시점이 어긋나 '같은 시점 산출' 전제가 깨진다.
    from tools import warp_gate_report as wgr

    calls = []

    def fake_sync(host, worker_env, *, pattern, dest, root="ocr_crops", **kw):
        calls.append((root, pattern, kw.get("timeout")))
        dest.mkdir(parents=True, exist_ok=True)
        return []

    # warp 하네스(pair_rows)가 쓰는 열은 job_id/row_index/status 3개뿐이다 — 최소 TSV.
    pairs_tsv = "job_id\trow_index\tstatus\n1\t0\tincluded\n"
    monkeypatch.setattr(wgr, "sync_remote_files", fake_sync)
    monkeypatch.setattr(
        wgr,
        "run_ssh",
        lambda host, script, **kw: (
            b"id\twarp_ok\timage_path\n1\ttrue\t/d/ocr_uploads/a.jpg\n"
            if "ocr_jobs" in script
            else pairs_tsv.encode()
        ),
    )

    meta = wgr.fetch_all("h", "/b", "/w", tmp_path)

    assert ("ocr_crops", wgr.WARPED_GLOB, None) in calls
    assert ("ocr_uploads", wgr.UPLOADS_GLOB, wgr.UPLOADS_TIMEOUT_S) in calls
    assert meta["n_uploads"] == 0
    assert json.loads((tmp_path / wgr.PAIRS_NAME).read_text()) == [
        {"job_id": 1, "row_index": 0, "status": "included"}
    ]


def test_fetch_all_warns_when_pairs_axis_population_is_empty(tmp_path, monkeypatch, capsys):
    # ②-a(pairs) 모집단이 0이면 파서가 조용히 []를 돌려주고 _render_pair_impact가 "변화
    # 0건"이라는 거짓 초록을 낸다 — n_warped/n_uploads와 같은 모양의 경고가 필요하다
    # (Task 7이 crop-identity 축에 세운 MIN_OK_RATIO 침묵 붕괴 가드와 동일한 실패 모양).
    from tools import warp_gate_report as wgr

    monkeypatch.setattr(
        wgr,
        "sync_remote_files",
        lambda host, worker_env, *, pattern, dest, root="ocr_crops", **kw: (
            dest.mkdir(parents=True, exist_ok=True),
            [{"job_id": 1}],
        )[1],
    )
    monkeypatch.setattr(
        wgr,
        "run_ssh",
        lambda host, script, **kw: (
            b"id\twarp_ok\timage_path\n1\ttrue\t/d/ocr_uploads/a.jpg\n"
            if "ocr_jobs" in script
            else b"job_id\trow_index\tstatus\n"
        ),
    )

    wgr.fetch_all("h", "/b", "/w", tmp_path)

    out = capsys.readouterr().out
    assert "pairs" in out or "학습쌍" in out


def test_fetch_all_pairs_query_does_not_request_exclusion_reason(tmp_path, monkeypatch):
    # #63: exclusion_reason(migration_009)은 어떤 v* 태그에도 포함된 적이 없어 운영 DB에
    # 없다. warp 하네스가 쓰지도 않는 이 열을 curation_enrich.PAIRS_SQL(11열) 재사용으로
    # 요청하면 "Unknown column 'exclusion_reason'"로 fetch 전체가 죽는다(#60에서 실측).
    from tools import warp_gate_report as wgr

    seen_scripts = []

    def fake_run_ssh(host, script, **kw):
        seen_scripts.append(script)
        if "ocr_jobs" in script:
            return b"id\twarp_ok\timage_path\n1\ttrue\t/d/ocr_uploads/a.jpg\n"
        return b"job_id\trow_index\tstatus\n1\t0\tincluded\n"

    monkeypatch.setattr(wgr, "run_ssh", fake_run_ssh)
    monkeypatch.setattr(
        wgr,
        "sync_remote_files",
        lambda host, worker_env, *, pattern, dest, root="ocr_crops", **kw: (
            dest.mkdir(parents=True, exist_ok=True),
            [],
        )[1],
    )

    wgr.fetch_all("h", "/b", "/w", tmp_path)

    pairs_script = next(s for s in seen_scripts if "training_pairs" in s)
    assert wgr.WARP_PAIRS_SQL in pairs_script
    assert "exclusion_reason" not in pairs_script
    assert "crop_ref" not in pairs_script


# --- crop-identity CLI (Task 7) ---


def _snapshot(n=1):
    """replicate_rows 출력 shape의 최소 합성값."""
    return {
        "n_bands": 12,
        "n_new": n,
        "boxes": [[0, 10]] * n,
        "crop_sha": ["a"] * n,
        "crop_ink": [0.1] * n,
    }


def _fake_collect(snapshot, *, ok=None, total=None, skipped=None):
    """collect_crop_identity 대역 — (스냅샷, 모집단 통계) 튜플 계약을 그대로 흉내낸다."""
    ok = len(snapshot) if ok is None else ok
    stats = {"total": ok if total is None else total, "ok": ok, "skipped": skipped or {}}

    def _collect(cache, jobs=None, dump_dir=None):
        _collect.seen = {"cache": cache, "jobs": jobs, "dump_dir": dump_dir}
        return dict(snapshot), stats

    _collect.seen = {}
    return _collect


def _crop_identity_argv(tmp_path, *extra):
    return [
        "--cache",
        str(tmp_path),
        "crop-identity",
        "--out",
        str(tmp_path / "after.json"),
        *extra,
    ]


def test_crop_identity_exits_nonzero_when_a_snapshot_changed(tmp_path, monkeypatch):
    # 이 exit code가 DoD 4의 유일한 기계 게이트다 — 오배선되면 DoD 4가 조용히 통과한다.
    from tools import warp_gate_report as wgr

    before = tmp_path / "before.json"
    before.write_text(json.dumps({"59": {"n_new": 5, "boxes": [[10, 20]], "crop_sha": ["aa"]}}))
    monkeypatch.setattr(
        wgr,
        "collect_crop_identity",
        _fake_collect({"59": {"n_new": 5, "boxes": [[10, 21]], "crop_sha": ["aa"]}}),
    )
    with pytest.raises(SystemExit) as e:
        wgr.main(_crop_identity_argv(tmp_path, "--baseline", str(before)))
    assert e.value.code == 1


def test_crop_identity_exits_nonzero_when_a_job_vanished_from_the_snapshot(tmp_path, monkeypatch):
    # 잡이 통째로 사라지는 재워프 회귀가 게이트의 다른 한 축이다 — exit 조건에서 missing이
    # 빠지면 그 축이 조용히 무고정 상태가 된다.
    from tools import warp_gate_report as wgr

    entry = {"n_new": 5, "boxes": [[10, 20]], "crop_sha": ["aa"]}
    before = tmp_path / "before.json"
    before.write_text(json.dumps({"59": entry, "60": entry}))
    monkeypatch.setattr(wgr, "collect_crop_identity", _fake_collect({"60": entry}))
    with pytest.raises(SystemExit) as e:
        wgr.main(_crop_identity_argv(tmp_path, "--baseline", str(before)))
    assert e.value.code == 1


def test_crop_identity_exits_zero_when_nothing_moved(tmp_path, monkeypatch):
    from tools import warp_gate_report as wgr

    snap = {"59": {"n_new": 5, "boxes": [[10, 20]], "crop_sha": ["aa"]}}
    before = tmp_path / "before.json"
    before.write_text(json.dumps(snap))
    monkeypatch.setattr(wgr, "collect_crop_identity", _fake_collect(snap))
    wgr.main(_crop_identity_argv(tmp_path, "--baseline", str(before)))


def test_crop_identity_writes_snapshot_and_prints_storage_path_without_baseline(
    tmp_path, monkeypatch
):
    # exit code 테스트 2건은 collect_crop_identity를 대역으로 바꿔 --out 실제 기록 여부를
    # 검증하지 못한다 — 여기서 파일 내용을 직접 확인해 그 배선을 고정한다.
    from tools import warp_gate_report as wgr

    monkeypatch.setattr(wgr, "collect_crop_identity", _fake_collect({"1": _snapshot(0)}))
    out = tmp_path / "snap.json"
    wgr.main(["--cache", str(tmp_path), "crop-identity", "--out", str(out)])
    assert json.loads(out.read_text()) == {"1": _snapshot(0)}


def test_crop_identity_forwards_the_jobs_filter_to_collect(tmp_path, monkeypatch):
    # --jobs가 collect_crop_identity로 안 전달되면 축 ②-b의 대상 좁히기(fail→pass 전환 잡
    # 전용 실행)가 조용히 전 잡 실행이 된다.
    from tools import warp_gate_report as wgr

    collect = _fake_collect({"59": _snapshot()})
    monkeypatch.setattr(wgr, "collect_crop_identity", collect)
    wgr.main(_crop_identity_argv(tmp_path, "--jobs", "59", "60"))
    assert collect.seen["jobs"] == [59, 60]


def test_crop_identity_rejects_a_jobs_flag_without_any_id(tmp_path, monkeypatch):
    # nargs="*"였을 때 `--jobs`만 주면 조용히 전 잡 실행이 됐다 — 대상을 좁히려던 의도와 정반대다.
    from tools import warp_gate_report as wgr

    monkeypatch.setattr(wgr, "collect_crop_identity", _fake_collect({"59": _snapshot()}))
    with pytest.raises(SystemExit) as e:
        wgr.main(_crop_identity_argv(tmp_path, "--jobs"))
    assert e.value.code == 2


def test_crop_identity_reports_changed_included_pairs_when_pairs_json_present(
    tmp_path, monkeypatch, capsys
):
    # spec §4.3 ②-a가 요구하는 것은 잡 단위가 아니라 행 단위 무변경이다 — 이 절이 DoD 4의
    # 직접 증거이므로 changed 잡의 행 인덱스가 실제로 대조표에 찍히는지 고정한다.
    from tools import warp_gate_report as wgr

    before = {"23": {"boxes": [[10, 20], [30, 40]], "crop_sha": ["aa", "bb"]}}
    after = {"23": {"boxes": [[10, 20], [31, 41]], "crop_sha": ["aa", "cc"]}}
    before_path = tmp_path / "before.json"
    before_path.write_text(json.dumps(before))
    (tmp_path / wgr.PAIRS_NAME).write_text(
        json.dumps(
            [
                {"job_id": 23, "row_index": 0, "status": "included"},
                {"job_id": 23, "row_index": 1, "status": "included"},
            ]
        )
    )
    monkeypatch.setattr(wgr, "collect_crop_identity", _fake_collect(after))

    with pytest.raises(SystemExit):
        wgr.main(_crop_identity_argv(tmp_path, "--baseline", str(before_path)))

    out = capsys.readouterr().out
    assert "| 23 | 1 | moved |" in out
    assert "변화 0건" not in out


def test_crop_identity_reports_included_pairs_that_vanished(tmp_path, monkeypatch, capsys):
    # 폴백이 학습쌍 행을 **없앤** 경우 — 조용히 건너뛰면 "변화 0건"이 찍혀 축 ②-a가 무너진
    # 사실이 리포트에서 사라진다.
    from tools import warp_gate_report as wgr

    before = {"23": {"boxes": [[10, 20], [30, 40]], "crop_sha": ["aa", "bb"]}}
    after = {"23": {"boxes": [[10, 20]], "crop_sha": ["aa"]}}
    before_path = tmp_path / "before.json"
    before_path.write_text(json.dumps(before))
    (tmp_path / wgr.PAIRS_NAME).write_text(
        json.dumps(
            [
                {"job_id": 23, "row_index": 0, "status": "included"},
                {"job_id": 23, "row_index": 1, "status": "included"},
            ]
        )
    )
    monkeypatch.setattr(wgr, "collect_crop_identity", _fake_collect(after))

    with pytest.raises(SystemExit):
        wgr.main(_crop_identity_argv(tmp_path, "--baseline", str(before_path)))

    out = capsys.readouterr().out
    assert "| 23 | 1 | vanished |" in out
    assert "변화 0건" not in out


def test_crop_identity_reports_no_pair_changes_when_none_moved(tmp_path, monkeypatch, capsys):
    from tools import warp_gate_report as wgr

    snap = {"23": {"boxes": [[10, 20]], "crop_sha": ["aa"]}}
    before_path = tmp_path / "before.json"
    before_path.write_text(json.dumps(snap))
    (tmp_path / wgr.PAIRS_NAME).write_text(
        json.dumps([{"job_id": 23, "row_index": 0, "status": "included"}])
    )
    monkeypatch.setattr(wgr, "collect_crop_identity", _fake_collect(snap))

    wgr.main(_crop_identity_argv(tmp_path, "--baseline", str(before_path)))

    assert "변화 0건" in capsys.readouterr().out


# --- crop-identity 모집단 건전성 게이트 ---


def test_crop_identity_prints_the_population_and_skip_counts(tmp_path, monkeypatch, capsys):
    # 모집단을 안 찍으면 '변화 0건'이 '정말 안 변했다'인지 '아무것도 못 봤다'인지 구분되지 않는다.
    from tools import warp_gate_report as wgr

    monkeypatch.setattr(
        wgr,
        "collect_crop_identity",
        _fake_collect({"1": _snapshot(), "2": _snapshot()}, total=3, skipped={"quad_missing": 1}),
    )
    wgr.main(_crop_identity_argv(tmp_path))

    out = capsys.readouterr().out
    assert "대상 잡 3" in out
    assert "ok 2" in out
    assert "quad_missing 1" in out


def test_crop_identity_exits_nonzero_when_the_snapshot_is_empty(tmp_path, monkeypatch):
    # uploads 캐시가 비었거나 fetch가 반쪽인 상태로 1차 실행하면 {} 베이스라인이 저장되고,
    # 2차에서 실측이 나와도 전부 added로만 분류돼 게이트가 exit 0 + "변화 0건"을 낸다.
    from tools import warp_gate_report as wgr

    monkeypatch.setattr(wgr, "collect_crop_identity", _fake_collect({}, ok=0, total=62))
    with pytest.raises(SystemExit) as e:
        wgr.main(_crop_identity_argv(tmp_path))

    assert "비었" in str(e.value)
    leftover = tmp_path / "after.json"
    assert not leftover.exists(), "쓸 수 없는 스냅샷을 베이스라인으로 남기면 안 된다"


def test_crop_identity_exits_nonzero_when_too_few_jobs_could_be_rewarped(tmp_path, monkeypatch):
    # 부분 fetch(사진 일부만 동기화)도 같은 침묵 붕괴를 만든다 — 비어 있지 않아도 모집단이
    # 얇으면 베이스라인으로 쓸 수 없다.
    from tools import warp_gate_report as wgr

    monkeypatch.setattr(
        wgr,
        "collect_crop_identity",
        _fake_collect({"1": _snapshot()}, total=10, skipped={"upload_missing": 9}),
    )
    with pytest.raises(SystemExit) as e:
        wgr.main(_crop_identity_argv(tmp_path))

    assert "하한" in str(e.value)


# --- crop-identity 수집기(재워프 순회) ---


def _ok_rewarp_cache(tmp_path, monkeypatch, jobs, *, snapshot=None, warped=None):
    """jobs.json을 시드하고 재워프·행재현을 대역으로 바꾼다 — 수집기 자체를 직접 부르기 위한 준비."""
    from tools import warp_gate_report as wgr

    _seed_cache(tmp_path, jobs=jobs)
    monkeypatch.setattr(
        wgr,
        "rewarp_job",
        lambda p, **kw: (
            (wgr.STATUS_OK, warped if warped is not None else object())
            if p.name == "ok.jpg"
            else ("quad_missing", None)
        ),
    )
    monkeypatch.setattr(wgr, "replicate_rows", lambda w: snapshot or _snapshot())
    return wgr


def test_collect_crop_identity_keys_the_snapshot_by_job_id_string(tmp_path, monkeypatch):
    # 키가 int로 새면 --baseline JSON(키는 항상 문자열)과 대조했을 때 전 잡이 missing+added로
    # 갈라져 게이트가 통째로 무의미해진다.
    wgr = _ok_rewarp_cache(
        tmp_path,
        monkeypatch,
        [{"job_id": 59, "warp_ok": True, "image_path": "/r/ocr_uploads/ok.jpg"}],
    )

    snapshot, stats = wgr.collect_crop_identity(tmp_path)

    assert list(snapshot) == ["59"]
    assert snapshot["59"] == _snapshot()
    assert stats == {"total": 1, "ok": 1, "skipped": {}}


def test_collect_crop_identity_visits_only_the_requested_jobs(tmp_path, monkeypatch):
    # --jobs를 순회가 무시하면 전 잡을 재워프하고도 아무도 모른다(전달만 보는 CLI 테스트로는
    # 못 잡는다).
    wgr = _ok_rewarp_cache(
        tmp_path,
        monkeypatch,
        [
            {"job_id": 59, "warp_ok": True, "image_path": "/r/ocr_uploads/ok.jpg"},
            {"job_id": 60, "warp_ok": True, "image_path": "/r/ocr_uploads/ok.jpg"},
        ],
    )

    snapshot, stats = wgr.collect_crop_identity(tmp_path, jobs=[60])

    assert list(snapshot) == ["60"]
    assert stats["total"] == 1


def test_collect_crop_identity_skips_and_counts_every_job_it_cannot_rewarp(tmp_path, monkeypatch):
    # 재워프 실패·사진 없음·경로 탈출을 전부 침묵 스킵하면 얇은 스냅샷과 건강한 스냅샷이
    # 구분되지 않는다 — 사유별로 세어 돌려줘야 게이트가 판단할 수 있다.
    wgr = _ok_rewarp_cache(
        tmp_path,
        monkeypatch,
        [
            {"job_id": 1, "warp_ok": True, "image_path": "/r/ocr_uploads/ok.jpg"},
            {"job_id": 2, "warp_ok": True, "image_path": "/r/ocr_uploads/broken.jpg"},
            {"job_id": 3, "warp_ok": True, "image_path": None},
            {"job_id": 4, "warp_ok": True, "image_path": "/a/b/.."},
        ],
    )

    snapshot, stats = wgr.collect_crop_identity(tmp_path)

    assert list(snapshot) == ["1"]
    assert stats == {
        "total": 4,
        "ok": 1,
        "skipped": {
            "quad_missing": 1,
            wgr.STATUS_UPLOAD_MISSING: 1,
            wgr.STATUS_INVALID_IMAGE_PATH: 1,
        },
    }


# --- 육안검수 크롭 덤프 ---


def test_dump_crop_pngs_writes_exactly_the_pixels_that_were_hashed(tmp_path):
    # 덤프가 크롭 기하를 따로 소유하면 '해시한 것'과 '눈으로 본 것'이 갈라진다 —
    # 축 ②-b의 육안 근거가 조용히 다른 픽셀을 보여주게 된다.
    cv2 = pytest.importorskip("cv2", exc_type=ImportError)
    np = pytest.importorskip("numpy")
    from handwriting.grid_v4 import WARP_W
    from tools import warp_gate_report as wgr
    from tools.warp_gate_rows import item_crop

    warped = np.random.default_rng(0).integers(0, 256, (40, WARP_W, 3), dtype=np.uint8)
    boxes = [[0, 10], [20, 30]]

    wgr._dump_crop_pngs(tmp_path, 7, warped, boxes)

    saved = sorted((tmp_path / "job-7").glob("row-*.png"))
    assert [p.name for p in saved] == ["row-0.png", "row-1.png"]
    for path, box in zip(saved, boxes, strict=True):
        assert np.array_equal(cv2.imread(str(path)), item_crop(warped, box))


def test_crop_identity_dumps_crops_within_the_single_rewarp_pass(tmp_path, monkeypatch):
    # 덤프가 자기 순회를 다시 돌면 62잡 재워프 비용이 두 배가 되고, 두 순회의 스킵 술어가
    # 갈라지면 해시한 모집단과 눈으로 보는 모집단이 조용히 달라진다.
    pytest.importorskip("cv2", exc_type=ImportError)
    np = pytest.importorskip("numpy")
    from handwriting.grid_v4 import WARP_W

    wgr = _ok_rewarp_cache(
        tmp_path,
        monkeypatch,
        [{"job_id": 1, "warp_ok": True, "image_path": "/r/ocr_uploads/ok.jpg"}],
        warped=np.zeros((40, WARP_W, 3), np.uint8),
    )
    seen = []
    inner = wgr.rewarp_job
    monkeypatch.setattr(wgr, "rewarp_job", lambda p, **kw: (seen.append(p), inner(p, **kw))[1])

    dump_dir = tmp_path / "dump"
    wgr.main(_crop_identity_argv(tmp_path, "--dump-crops", str(dump_dir)))

    assert len(seen) == 1
    assert (dump_dir / "job-1" / "row-0.png").exists()


# --- CLI 입력 오류 메시지 ---


def test_crop_identity_explains_a_missing_out_directory(tmp_path, monkeypatch):
    # 같은 모듈 _upload_path는 친절한 메시지를 내는데 여기만 맨 트레이스백이었다.
    from tools import warp_gate_report as wgr

    monkeypatch.setattr(wgr, "collect_crop_identity", _fake_collect({"1": _snapshot()}))
    with pytest.raises(SystemExit) as e:
        wgr.main(
            ["--cache", str(tmp_path), "crop-identity", "--out", str(tmp_path / "no" / "s.json")]
        )

    assert "--out" in str(e.value)


def test_crop_identity_explains_a_missing_baseline_file(tmp_path, monkeypatch):
    from tools import warp_gate_report as wgr

    monkeypatch.setattr(wgr, "collect_crop_identity", _fake_collect({"1": _snapshot()}))
    with pytest.raises(SystemExit) as e:
        wgr.main(_crop_identity_argv(tmp_path, "--baseline", str(tmp_path / "nope.json")))

    assert "베이스라인" in str(e.value)
