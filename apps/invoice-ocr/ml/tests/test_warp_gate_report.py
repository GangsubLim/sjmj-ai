"""tools.warp_gate_report 순수 계층 단위테스트(ssh/cv2 비의존, 합성 데이터만)."""

import json

import pytest

from tools.warp_gate_report import main, parse_job_rows_tsv

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

    # training_pairs TSV는 PAIR_COLS(curation_enrich.py) 전체 열을 갖춰야 parse_pairs_tsv가
    # 파싱한다 — 부분 열만 주면 zip(strict=True)가 KeyError로 죽는다.
    pairs_tsv = (
        "id\tcrop_ref\tjob_id\trow_index\tdraft_label\tfinal_label\tcanonical_label\t"
        "supply\tstatus\texclusion_reason\treviewed_at\n"
        "1\tjob-1/row-0\t1\t0\tNULL\tNULL\tNULL\tNULL\tincluded\tNULL\tNULL\n"
    )
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
    assert json.loads((tmp_path / wgr.PAIRS_NAME).read_text())[0]["crop_ref"] == "job-1/row-0"
