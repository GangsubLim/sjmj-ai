"""tools.blank_crop_report CLI 배선 단위테스트(실 ssh·실 DB 비의존).

ssh/mysql 글루 자체는 비대상이다(warp_gate_report 선례). 여기서 고정하는 것은 글루를
부르는 **배선**이다 — 커맨드별 순서(select_targets → plan_updates), 종료 코드, 게이트 분기,
그리고 원격 실패가 stdout 파싱을 우회해 그대로 올라오는지(M4).
"""

import io
import json
import shlex
import tarfile

import pytest

from tools import blank_crop_report as bcr
from tools import cache_sync
from tools.blank_crop_calib import STATUS_CROP_MISSING, STATUS_CROP_UNREADABLE, STATUS_OK
from tools.blank_crop_report import (
    evaluate_cached,
    fetch_warnings,
    main,
    plan_apply,
)
from tools.remote import ENV_SSH_HOST, RemoteError, env_or

THRESHOLD = 0.01


def _rec(
    crop_ref="job-1/row-0",
    *,
    ratio=0.5,
    crop_status=STATUS_OK,
    pair_status="included",
    reason=None,
    pair_id=1,
    job_id=1,
    reviewed=False,
):
    return {
        "id": pair_id,
        "crop_ref": crop_ref,
        "job_id": job_id,
        "pair_status": pair_status,
        "exclusion_reason": reason,
        "curation_reviewed": reviewed,
        "crop_status": crop_status,
        "ratio": ratio,
    }


# --- fetch 빈 결과 가드 (L4) ---
# parse_pairs_tsv("")는 []다 — 원격 질의가 빈 결과를 주면 리포트가 "학습쌍 0"으로 태연히
# 렌더된다. fetch가 그 자리에서 말해줘야 한다(warp_gate_report.fetch_all 선례).


def test_fetch_warnings_flags_zero_pairs():
    assert any("training_pairs" in w for w in fetch_warnings(n_pairs=0, n_crops=12))


def test_fetch_warnings_flags_zero_crops():
    assert any("크롭" in w for w in fetch_warnings(n_pairs=12, n_crops=0))


def test_fetch_warnings_flags_both_axes_when_everything_is_empty():
    # 두 축은 원인이 다르다(DB 접속 대상 vs SJMJ_DATA_DIR) — 하나로 뭉치면 오진한다.
    assert len(fetch_warnings(n_pairs=0, n_crops=0)) == 2


def test_fetch_warnings_is_empty_when_both_axes_are_populated():
    assert fetch_warnings(n_pairs=12, n_crops=11) == []


# --- apply 계획 배선 순서 (M3) ---
# 검수 완료 잡 가드는 호출 순서에만 걸려 있다(plan_updates는 curation_reviewed를 보지 않는다).
# select_targets를 plan_updates 앞에 두지 않으면 검수 완료 잡을 기계가 조용히 쓴다.


def test_plan_apply_filters_reviewed_jobs_before_planning_updates():
    # 두 쌍 모두 빈 크롭이지만 검수 완료 잡(id 9)의 쌍은 계획에 들어오면 안 된다.
    records = [
        _rec("job-1/row-0", ratio=0.001, pair_id=1, job_id=1, reviewed=False),
        _rec("job-9/row-0", ratio=0.001, pair_id=9, job_id=9, reviewed=True),
    ]
    plan = plan_apply(records, THRESHOLD, recheck_reviewed=False)
    assert [u.pair_id for u in plan.updates] == [1]
    assert [r["crop_ref"] for r in plan.targets] == ["job-1/row-0"]


def test_plan_apply_includes_reviewed_jobs_when_rechecking():
    records = [
        _rec("job-1/row-0", ratio=0.001, pair_id=1, job_id=1, reviewed=False),
        _rec("job-9/row-0", ratio=0.001, pair_id=9, job_id=9, reviewed=True),
    ]
    plan = plan_apply(records, THRESHOLD, recheck_reviewed=True)
    assert [u.pair_id for u in plan.updates] == [1, 9]


def test_plan_apply_collects_holds_from_selected_targets_only():
    # 보류 2건이지만 하나는 검수 완료 잡이라 기본 실행의 게이트 대상이 아니다.
    records = [
        _rec("job-1/row-0", ratio=None, crop_status=STATUS_CROP_MISSING, job_id=1),
        _rec(
            "job-9/row-0", ratio=None, crop_status=STATUS_CROP_UNREADABLE, job_id=9, reviewed=True
        ),
    ]
    plan = plan_apply(records, THRESHOLD, recheck_reviewed=False)
    assert [r["crop_ref"] for r in plan.holds] == ["job-1/row-0"]


def test_plan_apply_carries_counts_from_plan_updates():
    # 보호 1 · 불변 1 · 변경 1로 비대칭을 둬 집계 축이 뒤바뀐 배선을 잡는다.
    records = [
        _rec("job-1/row-0", ratio=0.001, pair_id=1),
        _rec("job-1/row-1", ratio=0.001, pair_status="excluded", reason=None, pair_id=2),
        _rec("job-1/row-2", ratio=0.5, pair_status="included", reason=None, pair_id=3),
    ]
    plan = plan_apply(records, THRESHOLD, recheck_reviewed=False)
    assert [u.pair_id for u in plan.updates] == [1]
    assert plan.counts == {"protected": 1, "unchanged": 1}


# --- 캐시 평가: crop_ink_ratio 호출 전에 보류를 가른다 (spec §8) ---


def test_evaluate_cached_holds_missing_and_unreadable_crops_without_measuring(tmp_path):
    # crop_ink_ratio(None)은 .size 접근에서 AttributeError로 샌다 — 판정 불가는 잉크를
    # 재기 전에 crop_status로 갈라야 한다. 측정 1 · 손상 1 · 없음 1로 비대칭을 둔다.
    pytest.importorskip("cv2", exc_type=ImportError)
    import numpy as np

    pairs = [
        {"id": 1, "crop_ref": "job-1/row-0", "job_id": 1},
        {"id": 2, "crop_ref": "job-1/row-1", "job_id": 1},
        {"id": 3, "crop_ref": "job-1/row-2", "job_id": 1},
    ]
    (tmp_path / "pairs.json").write_text(json.dumps(pairs))
    for ref in ("job-1/row-0", "job-1/row-1"):
        png = bcr.crop_path(tmp_path, ref)
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(b"not-a-real-png")
    white = np.full((40, 300, 3), 255, dtype=np.uint8)

    def fake_imread(path):
        return None if path.endswith("row-1.png") else white

    records = evaluate_cached(tmp_path, imread=fake_imread)
    assert [r["crop_status"] for r in records] == [
        STATUS_OK,
        STATUS_CROP_UNREADABLE,
        STATUS_CROP_MISSING,
    ]
    assert records[0]["ratio"] == pytest.approx(0.0)
    assert [r["ratio"] for r in records[1:]] == [None, None]


# --- fetch 글루 배선 (M3) ---
# 파일을 지우는 유일한 경로다. 순수함수만 테스트하면 "crops/ 하위만 rmtree"라는 계약도,
# fetch_warnings가 실제로 불리는지도 아무것도 고정되지 않는다.


def _tar_bytes(names):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for n in names:
            payload = f"png:{n}".encode()
            info = tarfile.TarInfo(n)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def _pairs_tsv(refs):
    header = "id\tcrop_ref\tjob_id\tstatus\texclusion_reason\tcuration_reviewed"
    rows = [f"{i}\t{ref}\t1\tincluded\tNULL\t0" for i, ref in enumerate(refs, start=1)]
    return "\n".join([header, *rows]) + "\n"


def _fetch_stub(monkeypatch, *, refs=(), listed=None, tarred=None, db=None):
    """fetch의 원격 3콜(mysql·ls·tar)을 대역으로 갈아끼운다.

    refs는 DB의 crop_ref(확장자 없음), listed는 원격 ls가 돌려줄 파일명, tarred는 tar
    스트림에 실제로 담길 파일명 — 뒤 둘을 따로 두면 "원격에는 있는데 로컬엔 안 풀린"
    부분 실패를 재현할 수 있다.
    """
    listed = [f"{r}.png" for r in refs] if listed is None else list(listed)
    tarred = listed if tarred is None else list(tarred)

    def fake_db(host, script, **kwargs):
        if isinstance(db, Exception):
            raise db
        return _pairs_tsv(refs).encode()

    remote = iter([("\n".join(listed) + "\n").encode(), _tar_bytes(tarred)])

    monkeypatch.setattr(bcr, "run_ssh", fake_db)
    monkeypatch.setattr(cache_sync, "run_ssh", lambda host, script, **kw: next(remote))


def test_fetch_replaces_stale_crops_but_leaves_the_rest_of_the_cache_alone(tmp_path, monkeypatch):
    # 계약: 지우는 범위는 crops/ 하위뿐이다(--cache 상위는 사용자 소유).
    stale = tmp_path / "crops" / "job-9" / "row-0.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale")
    keep = tmp_path / "labels.csv"
    keep.write_text("crop_ref,label\n", encoding="utf-8")

    _fetch_stub(monkeypatch, refs=["job-1/row-0"])
    main(["--cache", str(tmp_path), "fetch"])

    assert not stale.exists()
    assert (tmp_path / "crops" / "job-1" / "row-0.png").exists()
    assert keep.read_text(encoding="utf-8") == "crop_ref,label\n"
    assert json.loads((tmp_path / "pairs.json").read_text())[0]["crop_ref"] == "job-1/row-0"
    assert json.loads((tmp_path / "meta.json").read_text())["n_crops"] == 1


def test_fetch_prints_empty_result_warnings(tmp_path, monkeypatch, capsys):
    # fetch_warnings가 실제로 fetch 경로에서 불려야 한다(순수함수 테스트만으론 안 고정된다).
    _fetch_stub(monkeypatch, refs=[], listed=[])
    main(["--cache", str(tmp_path), "fetch"])
    out = capsys.readouterr().out
    assert "training_pairs가 0건" in out
    assert "크롭 PNG가 0건" in out


def test_fetch_refuses_when_crops_dir_is_a_symlink(tmp_path, monkeypatch):
    # H1: rmtree는 심볼릭 링크에 OSError를 낸다 — ignore_errors=True가 그걸 먹으면 옛 크롭이
    # 전량 생존하고, 이어지는 추출은 링크 대상(=--cache 바깥)에 쓴다.
    cache = tmp_path / "cache"
    cache.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    stale = outside / "job-9" / "row-0.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale")
    (cache / "crops").symlink_to(outside, target_is_directory=True)

    _fetch_stub(monkeypatch, refs=["job-1/row-0"])
    with pytest.raises(RuntimeError, match="심볼릭 링크"):
        main(["--cache", str(cache), "fetch"])
    assert stale.read_bytes() == b"stale"
    assert not (outside / "job-1" / "row-0.png").exists()


def test_fetch_warns_when_fewer_files_land_locally_than_the_remote_listing(
    tmp_path, monkeypatch, capsys
):
    # H1: `크롭 {n_crops}`는 원격 ls 개수다 — 로컬이 반쪽이어도 성공처럼 보인다.
    _fetch_stub(
        monkeypatch,
        refs=["job-1/row-0", "job-1/row-1"],
        tarred=["job-1/row-0.png"],
    )
    main(["--cache", str(tmp_path), "fetch"])
    assert "원격 목록 2건" in capsys.readouterr().out


def test_fetch_invalidates_stale_manifest_before_touching_the_remote(tmp_path, monkeypatch):
    # M2: crops를 먼저 지우고 meta를 마지막에 쓰면, 중단 시 '새 크롭 + 옛 meta'가 남아
    # 리포트 헤더가 옛 fetched_at을 동기화 시각으로 찍는다.
    _cache(tmp_path, host="oldhost")
    _fetch_stub(monkeypatch, refs=[], db=RemoteError("ssh 실패(macmini, exit 255)"))
    with pytest.raises(RemoteError):
        main(["--cache", str(tmp_path), "fetch"])
    assert not (tmp_path / "meta.json").exists()
    assert not (tmp_path / "pairs.json").exists()


def test_fetch_refuses_a_cache_directory_that_is_not_ours(tmp_path, monkeypatch):
    # --cache는 무검증이면 `""`(=cwd)·`/`도 그대로 받아 그 안의 crops/를 rmtree 대상으로 삼는다.
    foreign = tmp_path / "somebody-elses"
    foreign.mkdir()
    (foreign / "important.txt").write_text("x")
    _fetch_stub(monkeypatch, refs=[])
    with pytest.raises(SystemExit, match="캐시가 아니다"):
        main(["--cache", str(foreign), "fetch"])
    assert (foreign / "important.txt").exists()


# --- CLI ---


def _cache(tmp_path, pairs=(), host=None):
    (tmp_path / "pairs.json").write_text(json.dumps(list(pairs)))
    meta = {"fetched_at": "t", "host": env_or(ENV_SSH_HOST) if host is None else host}
    (tmp_path / "meta.json").write_text(json.dumps(meta))
    return tmp_path


def _stub(monkeypatch, *, records=None, threshold=None, ssh=None):
    """평가·임계·원격을 대역으로 갈아끼우고 run_ssh 호출 기록을 돌려준다."""
    import handwriting.blank_crop as blank_crop

    monkeypatch.setattr(blank_crop, "BLANK_INK_MAX", threshold)

    def fake_evaluate(cache, **kwargs):
        if records is None:
            raise AssertionError("evaluate_cached가 불리면 안 된다")
        return [dict(r) for r in records]

    calls = []

    def fake_run_ssh(host, script, **kwargs):
        calls.append((host, script))
        if ssh is None:
            raise AssertionError("run_ssh가 불리면 안 된다")
        if isinstance(ssh, Exception):
            raise ssh
        return ssh

    monkeypatch.setattr(bcr, "evaluate_cached", fake_evaluate)
    monkeypatch.setattr(bcr, "run_ssh", fake_run_ssh)
    return calls


def test_report_without_cache_exits_with_fetch_guidance(tmp_path):
    # fetch 전에 report를 돌리면 맨 FileNotFoundError 대신 다음 행동을 지시해야 한다.
    with pytest.raises(SystemExit) as excinfo:
        main(["--cache", str(tmp_path), "report"])
    assert "fetch" in str(excinfo.value)


def test_apply_without_cache_exits_with_fetch_guidance(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        main(["--cache", str(tmp_path), "apply"])
    assert "fetch" in str(excinfo.value)


def test_apply_fails_fast_before_evaluating_or_touching_remote_when_threshold_is_none(
    tmp_path, monkeypatch
):
    # 임계 미확정이면 캐시 평가·DB 접근보다 먼저 멈춘다(spec §8). 대역은 불리면 실패한다.
    _stub(monkeypatch, records=None, threshold=None, ssh=None)
    with pytest.raises(RuntimeError, match="BLANK_INK_MAX"):
        main(["--cache", str(_cache(tmp_path)), "apply"])


def test_report_runs_without_threshold(tmp_path, monkeypatch):
    # report는 임계 없이도 동작해야 한다 — 임계 결정의 입력이기 때문이다.
    _stub(monkeypatch, records=[_rec(ratio=0.004)], threshold=None)
    main(["--cache", str(_cache(tmp_path)), "report"])
    assert "BLANK_INK_MAX: 미확정" in (tmp_path / "blank_crop_report.md").read_text()


def test_report_renders_threshold_read_from_blank_crop_module(tmp_path, monkeypatch):
    _stub(monkeypatch, records=[_rec(ratio=0.004)], threshold=0.02)
    main(["--cache", str(_cache(tmp_path)), "report"])
    assert "임계 BLANK_INK_MAX = 0.02000" in (tmp_path / "blank_crop_report.md").read_text()


def test_report_converts_unreadable_label_manifest_into_exit(tmp_path, monkeypatch):
    # read_labels_csv/load_labels는 ValueError를 던진다 — 종료 코드 변환은 CLI의 몫이다.
    _stub(monkeypatch, records=[_rec(ratio=0.004)], threshold=None)
    labels = tmp_path / "labels.csv"
    labels.write_text("ref,판정\njob-1/row-0,blank\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="labels.csv"):
        main(["--cache", str(_cache(tmp_path)), "report", "--labels", str(labels)])


def test_report_converts_unknown_label_ref_into_exit(tmp_path, monkeypatch):
    _stub(monkeypatch, records=[_rec("job-1/row-0", ratio=0.004)], threshold=None)
    labels = tmp_path / "labels.csv"
    labels.write_text("crop_ref,label\njob-9/row-9,blank\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="labels.csv"):
        main(["--cache", str(_cache(tmp_path)), "report", "--labels", str(labels)])


def test_apply_sends_conditional_update_through_run_ssh_and_exits_clean(tmp_path, monkeypatch):
    records = [_rec("job-3/row-0", ratio=0.001, pair_id=7, job_id=3)]
    calls = _stub(
        monkeypatch,
        records=records,
        threshold=THRESHOLD,
        ssh=b"pair_id\taffected\n7\t1\n",
    )
    main(["--host", "testhost", "--cache", str(_cache(tmp_path, host="testhost")), "apply"])
    (host, script) = calls[0]
    assert host == "testhost"
    # mysql_script가 SQL을 `-e <quoted>`로 싣는다 — 원격 셸이 볼 SQL을 되꺼내 단언한다.
    sql = shlex.split(script)[-1]
    assert sql.startswith("START TRANSACTION;")
    assert "UPDATE training_pairs SET status = 'excluded', exclusion_reason = 'blank_crop'" in sql
    assert "WHERE id = 7 AND status = 'included' AND exclusion_reason <=> NULL;" in sql
    assert sql.rstrip().endswith("COMMIT;")


def test_apply_skips_remote_call_when_nothing_to_change(tmp_path, monkeypatch):
    # 이미 목표 상태인 쌍만 있으면 운영 DB를 건드리지 않는다(대역이 불리면 실패한다).
    records = [_rec(ratio=0.5, pair_status="included", reason=None)]
    calls = _stub(monkeypatch, records=records, threshold=THRESHOLD, ssh=None)
    main(["--cache", str(_cache(tmp_path)), "apply"])
    assert calls == []


def test_apply_exits_non_zero_when_holds_remain(tmp_path, monkeypatch):
    records = [_rec(ratio=None, crop_status=STATUS_CROP_MISSING)]
    _stub(monkeypatch, records=records, threshold=THRESHOLD, ssh=None)
    with pytest.raises(SystemExit, match="보류"):
        main(["--cache", str(_cache(tmp_path)), "apply"])


def test_apply_exits_zero_on_holds_with_allow_holds(tmp_path, monkeypatch):
    records = [_rec(ratio=None, crop_status=STATUS_CROP_MISSING)]
    _stub(monkeypatch, records=records, threshold=THRESHOLD, ssh=None)
    main(["--cache", str(_cache(tmp_path)), "apply", "--allow-holds"])


def test_apply_exits_non_zero_on_conflict_even_with_allow_holds(tmp_path, monkeypatch):
    # affected 0 = 충돌(fetch 이후 사람이 PATCH함) — 우회 플래그를 두지 않는다.
    records = [_rec("job-3/row-0", ratio=0.001, pair_id=7, job_id=3)]
    _stub(monkeypatch, records=records, threshold=THRESHOLD, ssh=b"pair_id\taffected\n7\t0\n")
    with pytest.raises(SystemExit, match="충돌"):
        main(["--cache", str(_cache(tmp_path)), "apply", "--allow-holds"])


def test_apply_exits_non_zero_when_probe_returns_ids_outside_the_plan(tmp_path, monkeypatch):
    # M2: 계획 밖 id(unknown)를 배선하지 않으면 stale 출력이 섞여도 "충돌 0"으로 끝난다.
    records = [_rec("job-3/row-0", ratio=0.001, pair_id=7, job_id=3)]
    _stub(
        monkeypatch,
        records=records,
        threshold=THRESHOLD,
        ssh=b"pair_id\taffected\n7\t1\npair_id\taffected\n999\t1\n",
    )
    with pytest.raises(SystemExit):
        main(["--cache", str(_cache(tmp_path)), "apply", "--allow-holds"])


def test_apply_refuses_when_the_cache_was_fetched_from_another_host(tmp_path, monkeypatch):
    # H2: WHERE에 실리는 seen 상태는 "fetch한 DB에서 본 상태"다. 스키마·id 채번이 같은
    # 스테이징/운영 사이에서는 우연히 일치해 다른 DB에서 본 근거로 운영 행을 뒤집는다.
    records = [_rec("job-3/row-0", ratio=0.001, pair_id=7, job_id=3)]
    calls = _stub(monkeypatch, records=records, threshold=THRESHOLD, ssh=None)
    cache = _cache(tmp_path, host="staging")
    with pytest.raises(SystemExit, match="staging"):
        main(["--host", "prod", "--cache", str(cache), "apply"])
    assert calls == []


def test_apply_dry_run_prints_the_plan_without_touching_the_remote(tmp_path, monkeypatch):
    # M1: --recheck-reviewed는 파괴 범위를 넓히는데, 무엇이 몇 건 늘어나는지 사전에 볼
    # 방법이 없었다(요약 print가 쓰기 *뒤*에 있었다).
    records = [_rec("job-3/row-0", ratio=0.001, pair_id=7, job_id=3)]
    calls = _stub(monkeypatch, records=records, threshold=THRESHOLD, ssh=None)
    main(["--cache", str(_cache(tmp_path)), "apply", "--dry-run"])
    assert calls == []


def test_apply_prints_the_plan_summary_before_sending_updates(tmp_path, monkeypatch, capsys):
    records = [_rec("job-3/row-0", ratio=0.001, pair_id=7, job_id=3)]
    _stub(
        monkeypatch,
        records=records,
        threshold=THRESHOLD,
        ssh=RemoteError("ssh 실패(testhost, exit 1)"),
    )
    with pytest.raises(RemoteError):
        main(["--cache", str(_cache(tmp_path)), "apply"])
    out = capsys.readouterr().out
    assert "대상 1" in out
    assert "변경 예정 1" in out


def test_apply_reports_the_extra_scope_pulled_in_by_recheck_reviewed(tmp_path, monkeypatch, capsys):
    records = [
        _rec("job-1/row-0", ratio=0.001, pair_id=1, job_id=1, reviewed=False),
        _rec("job-9/row-0", ratio=0.001, pair_id=9, job_id=9, reviewed=True),
        _rec("job-9/row-1", ratio=0.5, pair_id=10, job_id=9, reviewed=True),
    ]
    _stub(monkeypatch, records=records, threshold=THRESHOLD, ssh=None)
    main(["--cache", str(_cache(tmp_path)), "apply", "--recheck-reviewed", "--dry-run"])
    # 검수 완료 잡 1개 · 쌍 2건이 추가로 딸려 들어왔고 그중 1건이 실제 변경 대상이다.
    assert "--recheck-reviewed로 추가된 잡 1 · 쌍 2 · 변경 예정 1" in capsys.readouterr().out


def test_apply_propagates_remote_failure_instead_of_trusting_stdout(tmp_path, monkeypatch):
    # M4: ROW_COUNT 프로브는 COMMIT 앞에서 찍힌다 — mysql이 비-0으로 죽어도 stdout엔
    # 이미 `affected 1`이 있다. 성공 판정은 run_ssh의 RemoteError가 먼저 막아야 한다.
    records = [_rec("job-3/row-0", ratio=0.001, pair_id=7, job_id=3)]
    _stub(
        monkeypatch,
        records=records,
        threshold=THRESHOLD,
        ssh=RemoteError("ssh 실패(testhost, exit 1)"),
    )
    with pytest.raises(RemoteError):
        main(["--cache", str(_cache(tmp_path)), "apply", "--allow-holds"])
