"""tools.curation_report fetch 글루·CLI 계층 단위테스트 (ssh/DB 비의존, 합성 데이터만).

분석 계층(파싱·버킷·조인·집계)의 테스트는 tests/test_curation_enrich.py에,
렌더 계층(render_report·reeval_notice 등)의 테스트는 tests/test_curation_render.py에 있다.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import (  # 합성 헬퍼는 분석 계층 테스트와 공유한다
    _CUR_VERSION,
    BANK,
    _enrich,
    _enriched_row,
    _four_vintages,
    _job,
    _pair,
    _reeval_meta,
    _row,
)
from tools.curation_cohort import REEVAL_STATES, is_item_failure
from tools.curation_render import reeval_notice
from tools.curation_report import (
    _BANK_PY,
    _CACHE_RECOVERY,
    _clear_reeval,
    _cmd_fetch,
    _failure_job_ids,
    _fetch_reeval,
    _load_enriched,
    _replace_atomically,
    bank_script,
    fetch_all,
    fetch_error_message,
    main,
    pull_images,
    reeval_cat_script,
    reeval_probe_script,
)
from tools.remote import RemoteError


def test_pull_images_noop_on_empty_job_ids(tmp_path):
    out_dir = pull_images("unused-host", "unused-env", tmp_path, [], with_originals=False)
    assert out_dir == tmp_path / "images"
    assert out_dir.is_dir()


# --- 판정 불가 소비자 회귀 (spec §3-C의 표 — 소비자 6곳) ---


def test_failure_job_ids_does_not_stampede_on_unevaluable_items():
    """전 잡 폭주 회귀 — 판정 불가를 실패로 세면 pull-images가 전 잡 크롭을 당긴다(실측 18잡)."""
    rows = [
        _enriched_row(job_id=1, label_bucket="unevaluable", amount_bucket="ok"),
        _enriched_row(
            job_id=2,
            crop_ref="job-2/row-0",
            label_bucket="unevaluable",
            amount_bucket="zero_drift",
        ),
        _enriched_row(
            job_id=3, crop_ref="job-3/row-0", status="excluded", label_bucket="unevaluable"
        ),
        _enriched_row(job_id=4, crop_ref="job-4/row-0", label_bucket="in_bank_miss"),
    ]
    assert _failure_job_ids(rows) == [2, 3, 4]  # 1은 판정 불가일 뿐 실패가 아니다


def test_row_missing_pairs_stay_in_failures_and_pull_images():
    rows = [
        _enriched_row(job_id=1, label_bucket="row_missing", amount_bucket=None),
        _enriched_row(
            job_id=2, crop_ref="job-2/row-0", label_bucket="unevaluable", amount_bucket="ok"
        ),
    ]
    assert _failure_job_ids(rows) == [1]  # 2는 판정 불가일 뿐 실패가 아니다
    assert [r["job_id"] for r in rows if is_item_failure(r)] == [1]


# --- era-aware 재판정 (spec §3-C — unevaluable의 생산 지점) ---


def test_row_missing_survives_an_unevaluable_cohort():
    """M1 계약 유지 — 데이터 정합 장애는 시점 판정 불가에 삼켜지지 않는다.

    plan Task 11의 _item_bucket 초안은 코호트를 row_missing보다 먼저 봐서, 스탬프 없는 잡
    (현재 데이터 전량)의 조인 결손을 unevaluable로 흡수한다. 그러면 row_missing이
    failures.jsonl·pull-images에서 통째로 사라진다(curation_cohort.DATA_INTEGRITY_
    FAILURE_BUCKETS 계약 위반). 그래서 row_missing을 코호트보다 먼저 판정한다.
    """
    pairs = [_pair(row_index=9, crop_ref="job-1/row-9")]
    enriched = _enrich(pairs, [_job(rows=[], retrieval_version=None)])
    assert enriched[0]["cohort"] == "unknown"
    assert enriched[0]["label_bucket"] == "row_missing"
    assert _failure_job_ids(enriched) == [1]


def test_unevaluable_jobs_do_not_stampede_the_failure_list():
    """전 잡 폭주 실증 — 스탬프 이전 잡을 대량으로 넣어도 실패 목록이 비어 있어야 한다."""
    pairs = [_pair(id=i, job_id=i, crop_ref=f"job-{i}/row-0") for i in range(1, 6)]
    jobs = [
        _job(job_id=i, rows=[_row(job=i, top5=[("타이어", 0.4)])], retrieval_version=None)
        for i in range(1, 6)
    ]
    enriched = _enrich(pairs, jobs)
    assert {r["label_bucket"] for r in enriched} == {"unevaluable"}
    assert _failure_job_ids(enriched) == []


# --- fetch/report 배선 (ssh는 비대상 — 스크립트 조립과 로컬 캐시 계약만) ---


def test_bank_script_cds_into_ml_root_and_shares_the_fingerprint_entry_point():
    script = bank_script("$HOME/.sjmj-ai/ml-worker.env", "/srv/ml")
    assert 'cd "/srv/ml"' in script  # python -c는 cwd를 sys.path에 넣는다
    assert "from handwriting import bank_id" in script
    # 지문 입력(파일명·배열 선택)까지 워커와 공유하는 단일 진입점을 부른다(M4).
    assert "bank_retrieval_version" in script
    # 페이로드 키 형태로 못박는다 — `"retrieval_version" in script`는 앞줄이 통과시킨
    # "bank_retrieval_version"의 부분문자열이라 단독으로 실패할 수 없는 항진이었다.
    # fetch_all이 `bank.get("retrieval_version")`로 읽으므로 이 키가 곧 계약이다.
    assert "'retrieval_version': version" in script
    assert "'retrieval_version_error': error" in script  # 실패 사유도 stdout으로 온다
    assert script.startswith("set -eu")  # env 부재 시 즉시 실패(source_env 관례)


def test_bank_script_isolates_a_fingerprint_failure_but_keeps_the_import_hard_failing():
    """M3 — 지문 계산 실패가 pairs/jobs 동기화까지 막으면 분석 도구만 전체 정지한다.

    운영 워커는 정확히 그 이유로 같은 실패를 진단 필드 하나로 격리한다
    (worker.main.retrieval_version_or_none). 다만 `handwriting` import 실패는 배포 누락
    신호이므로 hard-fail을 유지한다 — try 블록 밖에 둔다.
    """
    script = bank_script("$HOME/.sjmj-ai/ml-worker.env", "/srv/ml")
    assert "try:" in script and "except Exception" in script
    assert script.index("from handwriting import bank_id") < script.index("try:")
    assert "version = None" in script  # 실패는 지문 null로 내보낸다


def test_bank_script_body_is_valid_python_and_survives_double_quoting():
    """원격 스크립트는 테스트에서 실행되지 않는다 — 문법 오류·금지 문자가 조용히 배포된다.

    `_BANK_PY`는 셸 이중따옴표 안에 그대로 보간되므로 `"`·`$`·백틱·백슬래시를 쓸 수 없다(모듈
    상단 주석의 계약). 그중 하나라도 새면 fetch가 원격에서 문장이 깨진 채 실패하고, 문법 오류는
    지문 질의 전체를 죽인다 — 둘 다 로컬에서 compile 한 번으로 잡힌다.
    """
    compile(_BANK_PY, "<remote bank script>", "exec")
    assert [c for c in ('"', "$", "`", "\\") if c in _BANK_PY] == []


def test_remote_scripts_expand_a_tilde_ml_root_instead_of_quoting_it_literally():
    """Task 6 리뷰 M2 이관 — `SJMJ_REMOTE_ML_ROOT=~/…` 주입이 원격에서 즉시 실패하지 않게 한다."""
    for script in (
        bank_script("~/e.env", "~/sjmj-ai/apps/invoice-ocr/ml"),
        reeval_probe_script("~/sjmj-ai/apps/invoice-ocr/ml"),
        reeval_cat_script("~/sjmj-ai/apps/invoice-ocr/ml", "score.jsonl"),
    ):
        assert '"~/' not in script
        assert "$HOME/sjmj-ai/apps/invoice-ocr/ml" in script


@pytest.mark.parametrize(
    "evil", ['/srv/ml"; rm -rf /', "/srv/$(id)/ml", "/srv/`id`/ml", "/srv\\ml"]
)
def test_remote_scripts_refuse_a_path_that_breaks_out_of_the_double_quotes(evil):
    """C9 — 경로는 `cd "..."`·`source "..."`에 그대로 보간된다.

    `remote_path`가 "이중따옴표 안에서 안전"을 약속하면서 `"`·백틱·`$(`·백슬래시를 통과시키면
    문장이 깨지거나 원격에서 다른 명령이 돈다. 출처가 운영자 자신의 env(SJMJ_REMOTE_*)라
    공격면은 아니지만, 오타 하나가 조용히 다른 명령이 되는 것보다 즉시 실패가 낫다.
    `$HOME` 확장은 살아 있어야 하므로 `$`를 통째로 막지 않는다(아래 테스트가 그 경계를 잡는다).
    """
    for build in (
        lambda p: bank_script("~/e.env", p),
        reeval_probe_script,
        lambda p: reeval_cat_script(p, "score.jsonl"),
    ):
        with pytest.raises(ValueError, match="셸 특수문자"):
            build(evil)


def test_remote_scripts_still_allow_a_plain_dollar_expansion():
    # `$HOME/...`이 원격 셸에서 확장되는 것이 이 경로 관례의 전제다 — 함께 막으면 기본값이 깨진다.
    assert '"$HOME/sjmj-ai/ml/results/bank_update/score.jsonl"' in reeval_cat_script(
        "$HOME/sjmj-ai/ml", "score.jsonl"
    )


def test_reeval_probe_script_does_not_fail_when_the_directory_is_absent():
    # 부재는 정상 상태다(재평가 미실행) — 비0 종료로 fetch 전체를 죽이지 않는다.
    script = reeval_probe_script("/srv/ml")
    assert "exit 0" in script and "|| true" in script
    assert 'cd "/srv/ml/results/bank_update"' in script


def test_reeval_cat_script_double_quotes_the_remote_path():
    # 공백·셸 메타문자가 든 경로가 단어분리로 갈라지지 않게 한다.
    assert (
        reeval_cat_script("/srv/my ml", "score.jsonl")
        == 'cat "/srv/my ml/results/bank_update/score.jsonl"'
    )


_GROUP = ("reeval.jsonl", "reeval_meta.json", "meta.json")


def test_replace_atomically_writes_every_file_and_leaves_no_tmp(tmp_path):
    _replace_atomically(tmp_path, [(name, f"{name}-body".encode()) for name in _GROUP])
    assert [(tmp_path / name).read_bytes() for name in _GROUP] == [
        f"{name}-body".encode() for name in _GROUP
    ]
    assert list(tmp_path.glob("*.tmp")) == []


def test_replace_atomically_leaves_the_old_group_intact_when_one_write_fails(tmp_path):
    """반쪽만 새것인 캐시를 만들지 않는다 — 전부 tmp로 받은 뒤에 교체한다."""
    _replace_atomically(tmp_path, [(name, b"old") for name in _GROUP])
    with pytest.raises(TypeError):
        _replace_atomically(
            tmp_path,
            [
                ("reeval.jsonl", b"new"),
                ("reeval_meta.json", b"new"),
                ("meta.json", "bytes가 아니다"),
            ],
        )
    assert [(tmp_path / name).read_bytes() for name in _GROUP] == [b"old"] * len(_GROUP)
    assert list(tmp_path.glob("*.tmp")) == []


_PAIRS_TSV = (
    "id\tcrop_ref\tjob_id\trow_index\tdraft_label\tfinal_label\t"
    "canonical_label\tsupply\tstatus\texclusion_reason\treviewed_at\n"
    "1\tjob-1/row-0\t1\t0\t엔진오일\t엔진오일\t엔진오일\t100000\tincluded\tNULL\tNULL\n"
)
_JOBS_TSV = "id\timage_path\tresult\n1\t/data/up/1.jpeg\t" + json.dumps(
    {"rows": [], "warp_ok": True, "retrieval_version": _CUR_VERSION}, ensure_ascii=False
)
_REEVAL_BODIES = (
    ("score.jsonl", b'{"side": "after"}\n'),
    ("score_meta.json", b'{"n_pairs": 1}\n'),
)


def _fake_ssh(
    *,
    remote_names=(),
    bodies=_REEVAL_BODIES,
    retrieval_version=_CUR_VERSION,
    version_error=None,
    bank_error=None,
):
    """run_ssh 대역 — 스크립트 내용으로 질의를 구분한다(ssh 없이 fetch 배선만 닫는다).

    `bank_error`를 주면 지문 질의만 RemoteError로 실패시킨다 — pairs/jobs는 성공한 뒤 지문에서
    죽는 실제 순서를 그대로 재현해야 fetch_all의 예외 변환 배선을 지난다.
    """
    bank = {
        "size": 1,
        "counts": {"엔진오일": 1},
        "retrieval_version": retrieval_version,
        "retrieval_version_error": version_error,
    }

    def run(host, script):
        if "training_pairs ORDER BY" in script:
            return _PAIRS_TSV.encode()
        if "ocr_jobs" in script:
            return _JOBS_TSV.encode()
        if "PYTHON_BIN" in script:
            if bank_error:
                raise RemoteError(bank_error)
            return json.dumps(bank, ensure_ascii=False).encode()
        if "ls score.jsonl" in script:
            return " ".join(remote_names).encode()
        for name, body in bodies:
            if script.endswith(f'/{name}"'):
                return body
        raise AssertionError(f"예상 못 한 원격 스크립트: {script}")

    return run


def _fetch_all(cache):
    """fetch_all 호출 — 키워드 전용 시그니처를 테스트에서도 한 자리에만 적는다."""
    return fetch_all(
        host="h",
        backend_env="backend.env",
        worker_env="worker.env",
        ml_root="/srv/ml",
        cache=cache,
    )


def _assert_names_file_and_recovery(err, filename):
    """손상 메시지가 **파일명과 복구 절차를** 함께 말하는지 본다.

    파일명만 보던 단언은 테스트 이름이 약속한 "복구 절차"를 검증하지 않았다 — 지침을 지워도
    GREEN이었다. 문구는 상수(`_CACHE_RECOVERY`)로 대조해 기대값을 손으로 복제하지 않되, 상수가
    빈 문자열이 되면 `"" in text`가 항진이 되므로 비어있지 않음과 실제 조치(`fetch`)를 함께 본다.
    """
    text = str(err.value)
    assert filename in text, text
    assert _CACHE_RECOVERY and _CACHE_RECOVERY in text, text
    assert "fetch" in text, text  # 복구 절차가 실행할 명령을 가리킨다


def test_fetch_reeval_returns_both_bodies_when_the_server_has_the_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.curation_report.run_ssh",
        _fake_ssh(remote_names=("score.jsonl", "score_meta.json")),
    )
    assert _fetch_reeval("h", "/srv/ml", tmp_path) == (
        "present",
        [("reeval.jsonl", b'{"side": "after"}\n'), ("reeval_meta.json", b'{"n_pairs": 1}\n')],
    )


def test_fetch_reeval_reports_a_score_jsonl_without_meta(tmp_path, monkeypatch):
    # #53 이전 산출물 — 정상 경로이므로 죽지 않고 상태 어휘로 알린다.
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh(remote_names=("score.jsonl",)))
    assert _fetch_reeval("h", "/srv/ml", tmp_path) == ("no_meta", [])


def test_fetch_reeval_treats_a_lone_meta_as_absent(tmp_path, monkeypatch):
    """비자명 분기 — meta만 있고 jsonl이 없으면 no_meta가 아니라 absent다(해석할 레코드가 없다)."""
    monkeypatch.setattr(
        "tools.curation_report.run_ssh", _fake_ssh(remote_names=("score_meta.json",))
    )
    assert _fetch_reeval("h", "/srv/ml", tmp_path) == ("absent", [])


def test_fetch_reeval_clears_a_stale_local_pair_when_the_server_has_nothing(tmp_path, monkeypatch):
    """서버에서 산출물이 사라지면 로컬도 지운다 — 남으면 재평가가 유효한 것처럼 읽힌다."""
    (tmp_path / "reeval.jsonl").write_text("x", encoding="utf-8")
    (tmp_path / "reeval_meta.json").write_text("y", encoding="utf-8")
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh())
    assert _fetch_reeval("h", "/srv/ml", tmp_path) == ("absent", [])
    assert not (tmp_path / "reeval.jsonl").exists()
    assert not (tmp_path / "reeval_meta.json").exists()


def test_fetch_reeval_state_range_matches_the_literal_bijectively(tmp_path, monkeypatch):
    """C6 — production이 ReevalState/REEVAL_STATES를 쓰지 않아 철자 드리프트를 잡는 장치가 없었다
    (있던 테스트조차 상수를 손으로 적은 집합과만 대조해 생산자는 검증 밖이었다).

    생산자(`_fetch_reeval`)의 **실제 반환 집합**을 전수 입력으로 치역과 맞춘다 — 오타("presnt")도
    dead 상태도 잡힌다. 오타는 `_reeval_info`가 present 아닌 값으로 읽어 재평가를 fail-closed로
    버리고 리포트는 사유를 "미상"으로 오보한다. Cohort/COHORTS·ReevalReason도 같은 짝을 둔다.
    """
    server_states = [(), ("score.jsonl",), ("score.jsonl", "score_meta.json")]
    states = set()
    for names in server_states:
        monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh(remote_names=names))
        states.add(_fetch_reeval("h", "/srv/ml", tmp_path)[0])
    assert states == set(REEVAL_STATES)


def test_fetch_all_replaces_the_meta_together_with_the_reeval_pair(tmp_path, monkeypatch):
    """M2 — 두 파일을 **해석하는** meta.json이 원자 교체 밖에 있으면 한 벌이 반쪽만 원자적이다.

    중간 실패는 fail-closed라 수치는 오염되지 않지만, 사유가 stale로 오보되어 사용자가 몇십 분
    짜리 재채점으로 간다(H1과 같은 오조치).
    """
    monkeypatch.setattr(
        "tools.curation_report.run_ssh",
        _fake_ssh(remote_names=("score.jsonl", "score_meta.json")),
    )
    replaced: list[str] = []
    real_replace = os.replace

    def spy(src, dst):
        replaced.append(Path(dst).name)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    _fetch_all(tmp_path)
    assert replaced == ["reeval.jsonl", "reeval_meta.json", "meta.json"]


def test_fetch_all_rejects_positional_arguments(tmp_path, monkeypatch):
    """C3 — host·두 env 경로·ml_root는 동종 str 4개다. ml_root가 뒤에 끼어든 뒤로는 위치 인자
    하나만 밀려도 예외 없이 다른 파일을 source하거나 다른 디렉터리로 cd한다(조용한 오연결).
    """
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh())
    with pytest.raises(TypeError):
        fetch_all("h", "backend.env", "worker.env", "/srv/ml", tmp_path)


def test_fetch_all_syncs_the_cache_even_when_the_remote_fingerprint_is_null(tmp_path, monkeypatch):
    """M3 — 원격 지문 계산 실패(null)가 pairs/jobs 동기화까지 막지 않는다."""
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh(retrieval_version=None))
    meta = _fetch_all(tmp_path)
    assert meta["retrieval_version"] is None
    assert meta["reeval_state"] == "absent"
    assert json.loads((tmp_path / "pairs.json").read_text(encoding="utf-8"))[0]["job_id"] == 1
    assert list(tmp_path.glob("*.tmp")) == []


def test_fetch_all_keeps_the_remote_reason_for_a_null_fingerprint(tmp_path, monkeypatch):
    """C2 — 원격은 사유를 알지만 exit 0이라 run_ssh가 stderr를 버렸다(사유 유실). 사유를 stdout
    페이로드로 받아 캐시 meta에 남겨야 운영자가 원인(git SHA 부재/npz 결손/모델 접근 실패)을
    구분할 수 있다 — 없으면 "지문 미확정"만 보고 무엇을 고칠지 알 수 없다.
    """
    monkeypatch.setattr(
        "tools.curation_report.run_ssh",
        _fake_ssh(retrieval_version=None, version_error="FileNotFoundError: keys.npy"),
    )
    meta = _fetch_all(tmp_path)
    assert meta["retrieval_version_error"] == "FileNotFoundError: keys.npy"
    cached = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert cached["retrieval_version_error"] == "FileNotFoundError: keys.npy"


def test_fetch_all_leaves_the_reason_none_when_the_fingerprint_succeeded(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh())
    assert _fetch_all(tmp_path)["retrieval_version_error"] is None


def test_cmd_fetch_prints_the_remote_reason_next_to_the_fingerprint_notice(
    tmp_path, monkeypatch, capsys
):
    """C2 — meta에 담기만 하고 인쇄하지 않으면 운영자는 캐시 파일을 열어봐야 원인을 안다."""
    monkeypatch.setattr(
        "tools.curation_report.run_ssh",
        _fake_ssh(retrieval_version=None, version_error="RuntimeError: git SHA 없음"),
    )
    _cmd_fetch("h", "backend.env", "worker.env", "/srv/ml", tmp_path)
    out = capsys.readouterr().out
    assert "RuntimeError: git SHA 없음" in out
    assert "fetch" in out  # 지문 미확정 안내(조치)도 함께 나온다


def test_cmd_fetch_says_nothing_about_a_reason_when_the_fingerprint_is_known(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh())
    _cmd_fetch("h", "backend.env", "worker.env", "/srv/ml", tmp_path)
    out = capsys.readouterr().out
    assert "실패 사유" not in out
    assert _CUR_VERSION in out


def test_clear_reeval_removes_both_files_together(tmp_path):
    """서버에 재평가가 없으면 로컬의 두 파일을 지운다 — 남으면 유효한 것처럼 읽힌다."""
    (tmp_path / "reeval.jsonl").write_text("x", encoding="utf-8")
    (tmp_path / "reeval_meta.json").write_text("y", encoding="utf-8")
    _clear_reeval(tmp_path)
    assert not (tmp_path / "reeval.jsonl").exists()
    assert not (tmp_path / "reeval_meta.json").exists()
    _clear_reeval(tmp_path)  # 멱등 — 이미 없어도 실패하지 않는다


def _write_cache(
    tmp_path,
    *,
    pairs,
    jobs,
    bank_labels=None,
    retrieval_version=_CUR_VERSION,
    reeval=None,
    reeval_meta=None,
    reeval_state="absent",
):
    # 쓰기 인코딩을 대상 코드(_load_enriched·_read_reeval_files의 utf-8 읽기)와 맞춘다 — 기본
    # 인코딩에 맡기면 로케일이 utf-8이 아닌 환경에서 한글 라벨이 UnicodeEncodeError로 터진다.
    bank_labels = sorted(BANK) if bank_labels is None else bank_labels
    (tmp_path / "pairs.json").write_text(json.dumps(pairs, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "jobs.json").write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "bank.json").write_text(
        json.dumps(
            {"size": len(bank_labels), "counts": {lb: 1 for lb in bank_labels}}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    if reeval is not None:
        (tmp_path / "reeval.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in reeval) + "\n", encoding="utf-8"
        )
    if reeval_meta is not None:
        (tmp_path / "reeval_meta.json").write_text(
            json.dumps(reeval_meta, ensure_ascii=False), encoding="utf-8"
        )
    (tmp_path / "meta.json").write_text(
        json.dumps(
            {
                "fetched_at": "t",
                "host": "h",
                "bank_size": len(bank_labels),
                "bank_distinct": len(bank_labels),
                "retrieval_version": retrieval_version,
                "reeval_state": reeval_state,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_reeval_meta(tmp_path, **over):
    """캐시에 **실제로 쓰인 바이트** 기준으로 다이제스트를 다시 적는다.

    줄바꿈·직렬화 차이를 그대로 반영해야 게이트가 digest_mismatch로 기각하지 않는다. 같은
    6줄이 세 테스트에 복붙돼 있었고 그 사본들만 쓰기 인코딩이 빠져 있었다 — 읽기가 utf-8이므로
    쓰기도 여기서 한 번 못박는다(_write_cache와 같은 이유).
    """
    meta = _reeval_meta(score_jsonl_sha256=_digest(tmp_path / "reeval.jsonl"), **over)
    (tmp_path / "reeval_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def _stale_pair():
    """exclusion_reason 컬럼 신설 이전 fetch가 만든 pairs.json 행(그 키가 통째로 없다)."""
    return {k: v for k, v in _pair().items() if k != "exclusion_reason"}


def test_report_command_fails_fast_on_a_stale_pairs_cache(tmp_path):
    """구버전 캐시(exclusion_reason 키 없음)를 조용히 0으로 세지 말고 fail-fast.

    검사 지점이 `_load_enriched`(공통)에서 report 소비자 앞으로 좁혀졌으므로, 좁힌 뒤에도
    report 경로에서 실제로 발화하는지는 CLI 배선으로 확인해야 한다.
    """
    _write_cache(tmp_path, pairs=[_stale_pair()], jobs=[])
    with pytest.raises(ValueError) as err:
        main(["--cache", str(tmp_path), "report"])
    assert "구버전" in str(err.value)
    _assert_names_file_and_recovery(err, "pairs.json")


def test_pull_images_still_runs_off_a_stale_pairs_cache(tmp_path, monkeypatch):
    """크롭 검수는 구버전 캐시로도 돌아야 한다 — pull-images는 status만 읽는다.

    가드가 공통 경로(`_load_enriched`)에 있으면 이 경로까지 hard-fail하는데, 하필 그 상황이
    `fetch_error_message`가 "배포 전에는 기존 캐시로 검수 루프를 계속하라"고 안내하는 상황이다.
    """
    _write_cache(tmp_path, pairs=[_stale_pair()], jobs=[])
    pulled: list[list[int]] = []

    def fake_pull(host, backend_env, cache, job_ids, with_originals):
        pulled.append(job_ids)
        return cache / "images"

    monkeypatch.setattr("tools.curation_report.pull_images", fake_pull)
    main(["--cache", str(tmp_path), "pull-images"])
    assert pulled == [[1]]  # 조인 결손(row_missing)이 검수 대상으로 남는다
    assert (tmp_path / "images_index.md").exists()


def test_load_enriched_wires_the_current_fingerprint_so_pairs_stay_evaluable(tmp_path):
    """Task 11 리뷰 M5 이관 — 지문을 넘기지 않으면 CLI 리포트가 전량 unevaluable이 된다."""
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job(rows=[_row(top5=[("엔진오일", 0.9)])], retrieval_version=_CUR_VERSION)],
    )
    enriched, meta = _load_enriched(tmp_path)
    assert enriched[0]["cohort"] == "current_bank"
    assert enriched[0]["label_bucket"] == "ok"
    assert meta["reeval"]["state"] == "absent"


def test_load_enriched_adopts_a_consistent_reevaluation(tmp_path):
    records = _four_vintages()
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job(rows=[_row(top5=[("타이어", 0.3)])], retrieval_version="old")],
        reeval=records,
        reeval_meta=_reeval_meta(score_jsonl_sha256="placeholder"),
        reeval_state="present",
    )
    _rewrite_reeval_meta(tmp_path)
    enriched, meta = _load_enriched(tmp_path)
    assert meta["reeval"]["adopted"] is True
    assert meta["reeval"]["after"] == _CUR_VERSION
    assert enriched[0]["cohort"] == "reevaluated"
    assert enriched[0]["top5_labels"] == ["안가방", "공임"]


def test_load_enriched_flattens_the_nested_fingerprint_for_the_notice(tmp_path):
    """Task 12 리뷰 이관 — score_meta는 지문을 중첩으로 쓰고 reeval_notice는 평탄 키를 읽는다.

    재맵이 없으면 채택 문구가 지문 자리에 '?'를 인쇄한다(계산 A/표시 B).
    """
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job(rows=[_row(top5=[("타이어", 0.3)])], retrieval_version="old")],
        reeval=_four_vintages(),
        reeval_meta=_reeval_meta(),
        reeval_state="present",
    )
    _rewrite_reeval_meta(tmp_path)
    _, meta = _load_enriched(tmp_path)
    line = reeval_notice(meta)
    assert _CUR_VERSION in line and "현재와 일치" in line
    assert "?" not in line


def test_load_enriched_reports_a_score_jsonl_without_meta_as_a_normal_path(tmp_path):
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job(rows=[_row(top5=[("엔진오일", 0.9)])], retrieval_version=None)],
        reeval_state="no_meta",
    )
    enriched, meta = _load_enriched(tmp_path)
    assert meta["reeval"] == {
        "state": "no_meta",
        "adopted": False,
        "reason": None,
        "generated_at": None,
        "after": None,
        "scope": None,
        "n_pairs": None,
    }
    assert enriched[0]["cohort"] == "unknown"
    assert "score_meta.json" in reeval_notice(meta)


def test_load_enriched_discards_a_stale_reevaluation_but_keeps_current_bank_pairs(tmp_path):
    """§3-C stale 방어 — 재평가는 통째로 버리고 각 쌍을 스탬프 기준으로 재분기한다."""
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job(rows=[_row(top5=[("엔진오일", 0.9)])], retrieval_version=_CUR_VERSION)],
        reeval=_four_vintages(),
        reeval_meta=_reeval_meta(),
        reeval_state="present",
    )
    _rewrite_reeval_meta(tmp_path, retrieval_version={"before": "x", "after": "older"})
    enriched, meta = _load_enriched(tmp_path)
    assert meta["reeval"]["adopted"] is False and meta["reeval"]["reason"] == "stale"
    # 스탬프가 현재와 같은 잡은 current_bank로 남는다 — 낡은 재평가가 없어도 그 잡은 현재
    # retrieval 상태로 추론된 것이다.
    assert enriched[0]["cohort"] == "current_bank"
    assert enriched[0]["label_bucket"] == "ok"


@pytest.mark.parametrize("body", ["[1, 2]", '"corrupt"', "null"])
def test_load_enriched_rejects_a_reeval_meta_that_is_not_an_object(tmp_path, body):
    """H2 — dict가 아닌 meta를 게이트 안쪽까지 흘리면 AttributeError로 도구가 통째로 죽는다.

    parse_reeval_jsonl이 같은 클래스를 경계에서 막는 이유와 같다(원인이 파싱 경계에서
    멀어진다). `null`은 게이트가 no_meta로 정상 처리하는데도 _reeval_info가 먼저 죽었다.
    """
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job()],
        reeval=_four_vintages(),
        reeval_meta={},
        reeval_state="present",
    )
    (tmp_path / "reeval_meta.json").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError) as err:
        _load_enriched(tmp_path)
    _assert_names_file_and_recovery(err, "reeval_meta.json")


def test_load_enriched_names_the_file_and_the_recovery_when_the_reeval_meta_is_corrupt(tmp_path):
    """H2 — 손상 파일의 JSONDecodeError가 raw로 새면 어느 파일을 어떻게 고치는지 알 수 없다."""
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job()],
        reeval=_four_vintages(),
        reeval_meta={},
        reeval_state="present",
    )
    (tmp_path / "reeval_meta.json").write_text("{not json}", encoding="utf-8")
    with pytest.raises(ValueError) as err:
        _load_enriched(tmp_path)
    _assert_names_file_and_recovery(err, "reeval_meta.json")


def test_load_enriched_names_the_file_and_the_recovery_when_the_reeval_jsonl_is_corrupt(tmp_path):
    """H2 — jsonl 쪽도 같은 지침을 붙인다(타입은 유지 — 즉시 실패 계약을 바꾸지 않는다).

    타입 유지가 계약의 절반이므로 예외 타입도 함께 본다: JSONDecodeError는 ValueError의
    하위형이라 호출자는 ValueError 하나로 잡지만, meta 쪽처럼 평범한 ValueError로 갈아치우면
    "즉시 실패 계약을 바꾸지 않는다"는 이 테스트의 전제가 조용히 깨진다.
    """
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job()],
        reeval=[],
        reeval_meta=_reeval_meta(),
        reeval_state="present",
    )
    (tmp_path / "reeval.jsonl").write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError) as err:
        _load_enriched(tmp_path)
    _assert_names_file_and_recovery(err, "reeval.jsonl")


# --- 지문 기능 이전 릴리스 안내 (C1 — 문구를 상상하지 않고 CPython에서 받아온다) ---


def _real_import_error_stderr(tmp_path, *, package_exists):
    """`from handwriting import bank_id`가 서버에서 **실제로** 내는 stderr를 받아온다.

    손으로 만든 문자열은 "우리가 상상한 문구"만 검증한다 — 그게 이 헬퍼가 막는 함정이다. 배포
    서버에는 `handwriting/`이 이미 있고 `bank_id.py`만 없어 CPython이 ModuleNotFoundError를 삼키고
    `ImportError: cannot import name 'bank_id' from 'handwriting'`을 낸다(= "No module named"가
    없다). 원격 스크립트도 `python -c`라 cwd가 sys.path에 들어가므로 cwd만 갈아 재현한다.
    """
    root = tmp_path / f"tree-{package_exists}"
    root.mkdir()
    if package_exists:
        (root / "handwriting").mkdir()
    proc = subprocess.run(
        [sys.executable, "-c", "from handwriting import bank_id"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""},
    )
    assert proc.returncode != 0, proc.stdout
    return proc.stderr


@pytest.mark.parametrize("package_exists", [True, False])
def test_fetch_error_message_recognizes_what_python_actually_raises(tmp_path, package_exists):
    """배포 누락의 두 형태를 실제 인터프리터 출력으로 닫는다.

    package_exists=True가 **주 시나리오**다(배포 서버에는 handwriting/이 이미 있다) — 예전
    테스트는 손으로 만든 "No module named 'handwriting.bank_id'"만 넣어서 이 경로가 안내문을
    내지 못하는 것을 놓쳤고, 운영자는 행동 지침 대신 raw traceback을 봤다.
    """
    stderr = _real_import_error_stderr(tmp_path, package_exists=package_exists)
    msg = fetch_error_message(stderr)
    assert msg is not None, stderr
    assert "Issue #49" in msg
    assert stderr.strip().splitlines()[-1] in msg  # 원본 마지막 줄(원인)을 삼키지 않는다


def test_fetch_error_message_ignores_an_unrelated_missing_module():
    """M1 — `No module named` 단독 매칭은 서버 venv의 numpy/torch 부재까지 오진한다.

    그 경우 배포는 이미 됐고 실제 원인은 venv라, "태그 배포 후 다시 실행하라"는 지침은
    사용자를 엉뚱한 조치로 보낸다.
    """
    stderr = "ssh 실패(macmini, exit 1): ModuleNotFoundError: No module named 'numpy'"
    assert fetch_error_message(stderr) is None


def test_fetch_error_message_returns_none_for_unrelated_failures():
    assert fetch_error_message("ssh 실패(macmini, exit 255): Connection refused") is None


def test_fetch_all_turns_a_pre_release_import_failure_into_a_guide(tmp_path, monkeypatch):
    """C21 — 순수 헬퍼만 테스트하던 배선(fetch_all의 except RemoteError → RuntimeError)을 실행한다.

    이 배선이 없으면(또는 판정이 발화하지 않으면) 운영자는 안내문 대신 원격 traceback을 본다.
    """
    stderr = _real_import_error_stderr(tmp_path, package_exists=True)
    monkeypatch.setattr(
        "tools.curation_report.run_ssh", _fake_ssh(bank_error=f"ssh 실패(h, exit 1): {stderr}")
    )
    with pytest.raises(RuntimeError, match="Issue #49"):
        _fetch_all(tmp_path)


def test_fetch_all_reraises_an_unrelated_remote_failure_as_is(tmp_path, monkeypatch):
    """무관한 실패를 RuntimeError로 갈아치우면 원인(ssh 계층)과 예외 타입이 함께 지워진다."""
    monkeypatch.setattr(
        "tools.curation_report.run_ssh",
        _fake_ssh(bank_error="ssh 실패(h, exit 255): Connection refused"),
    )
    with pytest.raises(RemoteError, match="Connection refused"):
        _fetch_all(tmp_path)
