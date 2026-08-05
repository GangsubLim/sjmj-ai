"""tools.curation_report fetch 글루·CLI 계층 단위테스트 (ssh/DB 비의존, 합성 데이터만).

분석 계층(파싱·버킷·조인·집계)의 테스트는 tests/test_curation_enrich.py에,
렌더 계층(render_report·reeval_notice 등)의 테스트는 tests/test_curation_render.py에 있다.
"""

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tests.conftest import (  # 합성 헬퍼는 분석 계층 테스트와 공유한다
    _CUR_VERSION,
    BANK,
    _correction,
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
    _CACHE_REFETCH,
    PullResult,
    _clear_reeval,
    _cmd_fetch,
    _failure_job_ids,
    _fetch_reeval,
    _load_enriched,
    _replace_atomically,
    bank_script,
    crops_tar_script,
    fetch_all,
    fetch_error_message,
    main,
    pull_images,
    reeval_cat_script,
    reeval_probe_script,
)
from tools.remote import RemoteError, source_env


def test_pull_images_noop_on_empty_job_ids(tmp_path):
    result = pull_images("unused-host", "unused-env", tmp_path, [], with_originals=False)
    assert result.out_dir == tmp_path / "images"
    assert result.out_dir.is_dir()


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

    이전 `_item_bucket` 초안은 코호트를 row_missing보다 먼저 봐서, 스탬프 없는 잡
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
    """원격 스크립트 리뷰 M2 이관 — `SJMJ_REMOTE_ML_ROOT=~/…` 주입이 원격에서 즉시 실패하지 않게 한다."""
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
_CORRECTIONS_TSV = (
    "job_id\tn_corrections\trows_added\trows_dropped\tn_lines\timage_path\n"
    "1\t1\t2\t1\t3\t/data/up/1.jpeg\n"
)
_LABEL_SOURCES_TSV = (
    "job_id\tcrop_ref\tlabel_source\n1\tjob-1/row-0\ttop1_kept\n1\tjob-1/row-1\tNULL\n"
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
        # 각 분기는 해당 SQL에만 있는 고유 마커로 건다 — 배치 순서가 아니라 마커 자체가
        # 계약이다(M3). 예컨대 CORRECTIONS_SQL도 `FROM ocr_jobs`를 포함해 그 문자열은 jobs
        # 분기와 겹치므로 쓸 수 없다. 마커가 SQL에서 사라지면(SELECT 별칭 개명 등) 이 분기가
        # 조용히 매치를 잃고 아래 마지막 raise가 즉시 알린다.
        if "training_pairs ORDER BY" in script:
            return _PAIRS_TSV.encode()
        if "AS n_corrections" in script:
            return _CORRECTIONS_TSV.encode()
        if "JSON_UNQUOTE(result_json)" in script:
            return _JOBS_TSV.encode()
        if "jt.label_source AS label_source" in script:
            return _LABEL_SOURCES_TSV.encode()
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
    GREEN이었다. 문구는 상수(`_CACHE_REFETCH`)로 대조해 기대값을 손으로 복제하지 않되, 상수가
    빈 문자열이 되면 `"" in text`가 항진이 되므로 비어있지 않음과 실제 조치(`fetch`)를 함께 본다.
    대조 대상이 `_CACHE_RECOVERY`(진단+절차)가 아니라 `_CACHE_REFETCH`(절차)인 이유: 손상이
    아닌 구버전 캐시 가드는 진단 문구 없이 절차만 싣는다(둘 다 이 절차로 끝난다).
    """
    text = str(err.value)
    assert filename in text, text
    assert _CACHE_REFETCH and _CACHE_REFETCH in text, text
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
    짜리 재채점으로 간다(H1과 같은 오조치). 이 단언 목록의 완전성이 곧 계약이다 — corrections.json
    이 이 원자 교체 한 벌 밖(별개 축)이라는 사실은 목록에 그 이름이 없다는 것으로 보장된다.
    corrections.json이 이 그룹에 섞이면 이 테스트가 먼저 깨진다.
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


def test_fetch_all_caches_the_correction_history_as_a_fourth_source(tmp_path, monkeypatch):
    """AC 1 — fetch가 교정 이력을 동기화해 캐시에 남긴다(새 env 없이 기존 글루 재사용)."""
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh())
    _fetch_all(tmp_path)
    cached = json.loads((tmp_path / "corrections.json").read_text(encoding="utf-8"))
    assert cached == [
        {
            "job_id": 1,
            "n_corrections": 1,
            "has_correction": True,
            "rows_added": 2,
            "rows_dropped": 1,
            "n_lines": 3,
            "draft_rows": 4,
            "confirmed_rows": 5,
            "image_path": "/data/up/1.jpeg",
        }
    ]


def test_fetch_all_caches_the_label_sources_as_a_fifth_source(tmp_path, monkeypatch):
    """AC — fetch가 조작 출처를 동기화해 캐시에 남긴다(신규 env 0 · 기존 mysql 글루 재사용)."""
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh())
    _fetch_all(tmp_path)
    cached = json.loads((tmp_path / "label_sources.json").read_text(encoding="utf-8"))
    assert cached == [
        {"job_id": 1, "crop_ref": "job-1/row-0", "label_source": "top1_kept"},
        {"job_id": 1, "crop_ref": "job-1/row-1", "label_source": None},
    ]


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
    corrections=None,
    label_sources=None,
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
    # 네 번째 소스. 기본값은 빈 목록 — 가드 테스트만 이 파일을 일부러 만들지 않는다.
    (tmp_path / "corrections.json").write_text(
        json.dumps(corrections or [], ensure_ascii=False), encoding="utf-8"
    )
    # 다섯 번째 소스. 기본값은 빈 목록 — 가드 테스트만 이 파일을 일부러 지우거나 망가뜨린다.
    (tmp_path / "label_sources.json").write_text(
        json.dumps(label_sources or [], ensure_ascii=False), encoding="utf-8"
    )
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
        return PullResult(cache / "images", saved=0, failed=0)

    monkeypatch.setattr("tools.curation_report.pull_images", fake_pull)
    main(["--cache", str(tmp_path), "pull-images"])
    assert pulled == [[1]]  # 조인 결손(row_missing)이 검수 대상으로 남는다
    assert (tmp_path / "images_index.md").exists()


def test_report_command_fails_fast_on_a_cache_without_the_correction_history(tmp_path):
    """AC 7 — 구버전 캐시(교정 이력 없음)에서 report가 조용히 통과하지 않는다."""
    _write_cache(tmp_path, pairs=[_pair()], jobs=[_job(rows=[_row()])])
    (tmp_path / "corrections.json").unlink()
    with pytest.raises(ValueError) as err:
        main(["--cache", str(tmp_path), "report"])
    _assert_names_file_and_recovery(err, "corrections.json")
    # 이 가드가 주로 잡는 것은 멀쩡한 구버전 캐시다 — "손상이다"를 단정하면 운영자를 엉뚱한
    # 조치(캐시 삭제)로 보내고, 같은 메시지 앞줄이 적는 사유(구버전·중단된 fetch)와도 어긋난다.
    assert "손상" not in str(err.value)


def test_report_command_names_the_file_and_the_recovery_when_corrections_are_corrupt(tmp_path):
    """M4 — corrections.json은 비원자 쓰기라 중단된 fetch가 잘린 파일을 남길 수 있다.

    `_reeval_info`의 손상 가드(H2)와 같은 관용구를 요구한다 — raw JSONDecodeError가 새면
    어느 파일을 어떻게 고치는지 알 수 없다.
    """
    _write_cache(tmp_path, pairs=[_pair()], jobs=[_job(rows=[_row()])])
    (tmp_path / "corrections.json").write_text("{not json}", encoding="utf-8")
    with pytest.raises(ValueError) as err:
        main(["--cache", str(tmp_path), "report"])
    _assert_names_file_and_recovery(err, "corrections.json")


@pytest.mark.parametrize("body", ["{}", '"corrupt"', "null"])
def test_report_command_rejects_a_corrections_cache_that_is_not_an_array(tmp_path, body):
    """M4의 나머지 절반 — 파싱은 되지만 배열이 아닌 캐시도 경계에서 막는다.

    `_reeval_info`의 "JSON 객체가 아니다" 가드와 같은 축이다(그쪽은 `[1, 2]`/`"corrupt"`/`null`로
    닫혀 있다). 통과시키면 `{}` 캐시가 렌더 안쪽에서 파일명도 복구 절차도 없는 TypeError로 죽어
    원인이 파싱 경계에서 멀어진다.
    """
    _write_cache(tmp_path, pairs=[_pair()], jobs=[_job(rows=[_row()])])
    (tmp_path / "corrections.json").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError) as err:
        main(["--cache", str(tmp_path), "report"])
    _assert_names_file_and_recovery(err, "corrections.json")


def test_report_command_fails_fast_on_a_cache_without_the_label_sources(tmp_path):
    """구버전 캐시(조작 출처 없음)에서 report가 조용히 "미기록 0건"을 인쇄하지 않는다."""
    _write_cache(tmp_path, pairs=[_pair()], jobs=[_job(rows=[_row()])])
    (tmp_path / "label_sources.json").unlink()
    with pytest.raises(ValueError) as err:
        main(["--cache", str(tmp_path), "report"])
    _assert_names_file_and_recovery(err, "label_sources.json")
    # 이 가드가 주로 잡는 것은 손상이 아니라 구버전 캐시다(_require_corrections와 같은 규약).
    assert "손상" not in str(err.value)


@pytest.mark.parametrize("body", ["{not json}", "{}", '"corrupt"', "null"])
def test_report_command_rejects_a_broken_label_sources_cache(tmp_path, body):
    """비원자 `_write_json`으로 쓰이므로 중단된 fetch가 잘린 파일을 남기는 것이 현실적 상태다."""
    _write_cache(tmp_path, pairs=[_pair()], jobs=[_job(rows=[_row()])])
    (tmp_path / "label_sources.json").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError) as err:
        main(["--cache", str(tmp_path), "report"])
    _assert_names_file_and_recovery(err, "label_sources.json")


def test_report_command_writes_the_label_source_section(tmp_path):
    """AC — 캐시의 조작 출처가 실제 리포트 파일까지 도달한다(로더 배선 확인)."""
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job(rows=[_row(top5=[("엔진오일", 0.9)])])],
        corrections=[_correction(job_id=1, n_lines=1)],
        label_sources=[{"job_id": 1, "crop_ref": "job-1/row-0", "label_source": "top1_kept"}],
    )
    main(["--cache", str(tmp_path), "report"])
    report = (tmp_path / "report.md").read_text()
    assert "## 조작 출처" in report
    assert "| top1_kept | 1 | 100.0% |" in report


def test_pull_images_still_runs_without_the_label_sources(tmp_path, monkeypatch):
    """가드는 report 분기에만 둔다 — 공통 경로에서 막으면 크롭 검수 루프까지 함께 죽는다.

    하필 그 상황이 `fetch_error_message`가 "기존 캐시로 검수 루프를 계속하라"고 안내하는
    상황이다(corrections 축의 같은 테스트와 골격이 같고 축만 다르다).
    """
    _write_cache(tmp_path, pairs=[_pair()], jobs=[_job(rows=[_row()])])
    (tmp_path / "label_sources.json").unlink()
    pulled: list[list[int]] = []

    def fake_pull(host, backend_env, cache, job_ids, with_originals):
        pulled.append(job_ids)
        return PullResult(cache / "images", saved=0, failed=0)

    monkeypatch.setattr("tools.curation_report.pull_images", fake_pull)
    main(["--cache", str(tmp_path), "pull-images"])
    assert pulled == [[1]]


def test_pull_images_still_runs_without_the_correction_history(tmp_path, monkeypatch):
    """가드는 report 경로에만 둔다 — 공통 경로에서 막으면 크롭 검수 루프까지 함께 죽는다.

    기존 `test_pull_images_still_runs_off_a_stale_pairs_cache`와 골격이 같지만 축이 다르다
    (pairs.json 구버전 키 vs corrections.json 파일 부재). 두 축을 한 테스트로 합치지 않는다.
    """
    _write_cache(tmp_path, pairs=[_pair()], jobs=[_job(rows=[_row()])])
    (tmp_path / "corrections.json").unlink()
    pulled: list[list[int]] = []

    def fake_pull(host, backend_env, cache, job_ids, with_originals):
        pulled.append(job_ids)
        return PullResult(cache / "images", saved=0, failed=0)

    monkeypatch.setattr("tools.curation_report.pull_images", fake_pull)
    main(["--cache", str(tmp_path), "pull-images", "--jobs", "1"])
    assert pulled == [[1]]


def test_report_command_passes_the_correction_history_to_the_renderer(tmp_path, capsys):
    """배선 회귀 — 캐시를 읽고도 렌더에 넘기지 않으면 새 절이 통째로 빈다."""
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job(rows=[_row(top5=[("엔진오일", 0.9)])])],
        corrections=[_correction(job_id=1, n_lines=3, rows_added=2, rows_dropped=1)],
    )
    main(["--cache", str(tmp_path), "report"])
    assert "확정 잡 1개(쌍 보유 1 / 쌍 0개 0)" in capsys.readouterr().out


def test_load_enriched_wires_the_current_fingerprint_so_pairs_stay_evaluable(tmp_path):
    """리뷰 M5 이관 — 지문을 넘기지 않으면 CLI 리포트가 전량 unevaluable이 된다."""
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
    """리뷰 이관 — score_meta는 지문을 중첩으로 쓰고 reeval_notice는 평탄 키를 읽는다.

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


# --- pull-images 원본 회수 (spec §6 — 쌍 0개·크롭 0개 잡) ---


def _tar_bytes(names):
    """합성 tar 스트림 — 기존 크롭 회수 경로가 회귀하지 않았는지 보는 입력."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name in names:
            info = tarfile.TarInfo(name)
            info.size = 3
            tf.addfile(info, io.BytesIO(b"png"))
    return buf.getvalue()


def test_crops_tar_script_narrows_the_tar_arguments_to_existing_directories():
    """크롭 디렉터리가 없으면 tar가 비0으로 죽어 --originals 분기에 도달조차 못 했다.

    조각별 부분 문자열 단언은 이음매(`; ` 결합·인용)를 못 잡아 이 슬라이스에서 반복해 새어
    나갔다 — 조립 결과 전문을 그대로 고정한다.
    """
    script = crops_tar_script("~/.sjmj-ai/backend.env", [34, 57])
    assert script == (
        source_env("~/.sjmj-ai/backend.env")
        # 크롭 루트 자체가 없으면 조용한 no-op으로 흡수한다(reeval_probe_script와 같은 관용구).
        + 'cd "$SJMJ_DATA_DIR/ocr_crops" 2>/dev/null || exit 0; '
        # 누산은 위치 인자로 한다 — zsh는 SH_WORD_SPLIT이 기본 off라 문자열 누산 + 비인용
        # 확장이 단어분리되지 않고 " job-34 job-57" 하나를 찾다 실패한다(sh/bash/zsh 실측).
        + "set --; "
        + 'for d in job-34 job-57; do [ -d "$d" ] && set -- "$@" "$d"; done; '
        + "[ $# -gt 0 ] || exit 0; "  # 하나도 없으면 tar를 아예 실행하지 않는다
        + 'tar -cf - "$@"'
    )


def test_pull_images_still_extracts_the_crops_it_used_to(tmp_path, monkeypatch):
    """회귀 방지 — 디렉터리 선별은 좁히기만 한다(존재하는 것은 전부 통과)."""
    monkeypatch.setattr(
        "tools.curation_report.run_ssh", lambda host, script: _tar_bytes(["job-34/row-0.png"])
    )
    result = pull_images("h", "e.env", tmp_path, [34], with_originals=False)
    assert (result.out_dir / "job-34" / "row-0.png").read_bytes() == b"png"


def test_pull_images_refuses_a_tar_member_that_escapes_the_destination(tmp_path, monkeypatch):
    """이 태스크의 최대 리스크는 원격이 준 tar를 그대로 푸는 것이다 — 경로 탈출은 막혀야 한다."""
    monkeypatch.setattr(
        "tools.curation_report.run_ssh", lambda host, script: _tar_bytes(["../evil.png"])
    )
    with pytest.raises(tarfile.OutsideDestinationError):
        pull_images("h", "e.env", tmp_path, [34], with_originals=False)
    assert not (tmp_path / "evil.png").exists()


def test_pull_images_warns_when_no_crops_came_back(tmp_path, monkeypatch, capsys):
    """M1 — 크롭 tar가 빈 출력이면 조용한 성공이 아니라 경고를 남긴다.

    문구는 원인 3종을 모두 연다 — 바로 위 주석이 적는 `SJMJ_DATA_DIR` 오설정도 같은 빈 출력으로
    오므로, 두 종만 열거하면 읽는 사람이 가장 흔한 오설정을 후보에서 지운다.
    """
    monkeypatch.setattr("tools.curation_report.run_ssh", lambda host, script: b"")
    pull_images("h", "e.env", tmp_path, [34], with_originals=False)
    out = capsys.readouterr().out
    assert "크롭 0건 회수(디렉터리 부재·크롭 미생성·SJMJ_DATA_DIR 오설정): [34]" in out


def test_pull_images_saves_the_original_of_a_job_without_pairs_or_crops(tmp_path, monkeypatch):
    """AC 4의 다음 액션이 실제로 수행 가능해야 한다 — tar는 빈 출력, 경로는 교정 이력 폴백."""
    _write_cache(
        tmp_path,
        pairs=[],
        jobs=[],
        corrections=[_correction(job_id=57, n_lines=0, rows_added=9)],
    )
    calls: list[str] = []

    def fake_ssh(host, script):
        calls.append(script)
        return b"" if "tar -cf -" in script else b"JPEGDATA"

    monkeypatch.setattr("tools.curation_report.run_ssh", fake_ssh)
    main(["--cache", str(tmp_path), "pull-images", "--jobs", "57", "--originals"])
    saved = tmp_path / "images" / "job-57" / "original.jpg"
    assert saved.read_bytes() == b"JPEGDATA"
    # 교정 이력의 image_path를 쓰되 옵션 종결자(`--`)를 붙인다 — `-`로 시작하는 경로는
    # cat의 옵션으로 해석되고, 정확히 `-`면 원격 stdin에서 timeout까지 멈춘다.
    assert "cat -- /data/up/57.jpeg" in calls  # shlex.quote는 안전한 경로를 그대로 둔다


def test_pull_images_prefers_the_jobs_cache_over_the_corrections_fallback(tmp_path, monkeypatch):
    _write_cache(
        tmp_path,
        pairs=[],
        jobs=[{"job_id": 57, "image_path": "/from/jobs.jpeg", "result": {"rows": []}}],
        corrections=[_correction(job_id=57, n_lines=0, rows_added=9)],
    )
    calls: list[str] = []

    def fake_ssh(host, script):
        calls.append(script)
        return b"" if "tar -cf -" in script else b"J"

    monkeypatch.setattr("tools.curation_report.run_ssh", fake_ssh)
    pull_images("h", "e.env", tmp_path, [57], with_originals=True)
    assert any("/from/jobs.jpeg" in c for c in calls)
    assert not any("/data/up/57.jpeg" in c for c in calls)


def test_pull_images_warns_and_continues_when_a_job_is_in_neither_source(
    tmp_path, monkeypatch, capsys
):
    """한 잡 때문에 회수 전체를 죽이지 않는다."""
    _write_cache(
        tmp_path, pairs=[], jobs=[], corrections=[_correction(job_id=57, n_lines=0, rows_added=9)]
    )
    monkeypatch.setattr(
        "tools.curation_report.run_ssh",
        lambda host, script: b"" if "tar -cf -" in script else b"J",
    )
    pull_images("h", "e.env", tmp_path, [57, 99], with_originals=True)
    out = capsys.readouterr().out
    # 원본 경로를 못 찾은 잡을 이름과 함께 알린다 — 이음매까지 고정한다(부분 단언은 "99"가
    # 다른 문장에서 우연히 맞아도 통과한다).
    assert "원본 경로를 찾지 못한 잡(캐시에 없음): [99] — 나머지는 계속 회수한다" in out
    assert (tmp_path / "images" / "job-57" / "original.jpg").exists()  # 나머지는 계속 처리


def test_pull_images_falls_back_to_jobs_only_without_a_corrections_cache(tmp_path, monkeypatch):
    """구버전 캐시로도 크롭 검수 루프는 계속 돈다(§7의 '가드 규칙은 바뀌지 않는다')."""
    _write_cache(tmp_path, pairs=[], jobs=[{"job_id": 5, "image_path": "/a.jpeg", "result": {}}])
    (tmp_path / "corrections.json").unlink()
    monkeypatch.setattr(
        "tools.curation_report.run_ssh",
        lambda host, script: b"" if "tar -cf -" in script else b"J",
    )
    pull_images("h", "e.env", tmp_path, [5], with_originals=True)
    assert (tmp_path / "images" / "job-5" / "original.jpg").exists()


def test_pull_images_keeps_going_when_one_original_cat_fails(tmp_path, monkeypatch, capsys):
    """H4 — 사진이 지워진 잡 하나가 나머지 회수를 취소하지 않는다."""
    _write_cache(
        tmp_path,
        pairs=[],
        jobs=[],
        corrections=[_correction(job_id=57), _correction(job_id=58)],
    )

    def fake_ssh(host, script):
        if "tar -cf -" in script:
            return b""
        if "/data/up/57.jpeg" in script:
            raise RemoteError("ssh 실패(h, exit 1): cat: No such file")
        return b"J"

    monkeypatch.setattr("tools.curation_report.run_ssh", fake_ssh)
    pull_images("h", "e.env", tmp_path, [57, 58], with_originals=True)
    assert not (tmp_path / "images" / "job-57" / "original.jpg").exists()
    assert (tmp_path / "images" / "job-58" / "original.jpg").exists()
    # 실패한 잡·경로·원인이 한 줄에 함께 남아야 다음 조치를 판단할 수 있다.
    assert (
        "원본을 읽지 못한 잡 57(/data/up/57.jpeg): ssh 실패(h, exit 1): cat: No such file"
        " — 나머지는 계속 회수한다" in capsys.readouterr().out
    )


def test_crops_tar_script_rejects_job_ids_that_are_not_integers():
    """job_ids는 비인용 보간이라 문자열이 섞이면 원격에서 그대로 명령이 된다.

    현재 호출부는 argparse `type=int`·`int(...)`로 전부 정수지만 이 함수는 공개 함수라 그
    보장이 코드에 없다 — `remote_path`와 같은 fail-fast 관용구로 경계에서 막는다.
    """
    with pytest.raises(ValueError) as err:
        crops_tar_script("~/e.env", ["34; id #"])
    assert "job_ids" in str(err.value)


def _run_crops_script_body(job_ids, *, data_dir, cwd):
    """source_env 이후 본문만 실 셸에 태운다 — 원격 env 로드는 ssh 없이 재현할 수 없다."""
    env_file = "~/e.env"
    body = crops_tar_script(env_file, job_ids)[len(source_env(env_file)) :]
    return subprocess.run(
        ["/bin/sh", "-c", "set -eu; " + body],
        cwd=cwd,
        env={"SJMJ_DATA_DIR": str(data_dir)},
        capture_output=True,
        check=False,
    )


def test_crops_tar_script_exits_zero_in_a_real_shell_for_all_three_states(tmp_path):
    """전문 동치 테스트는 포맷만 고정한다 — 이 스크립트의 본래 성질은 실 셸로 잠근다.

    성질은 하나다: 어떤 상태에서도 비0으로 죽지 않는다(죽으면 RemoteError가 나 `--originals`
    분기에 도달하지 못한다). 문자열을 갈아끼우는 수정이 이 성질을 깨면 여기서 걸린다.
    """
    data_dir = tmp_path / "data"
    (data_dir / "ocr_crops" / "job-34").mkdir(parents=True)
    present = _run_crops_script_body([34, 57], data_dir=data_dir, cwd=tmp_path)
    none = _run_crops_script_body([99], data_dir=data_dir, cwd=tmp_path)
    no_root = _run_crops_script_body([34], data_dir=tmp_path / "nowhere", cwd=tmp_path)
    assert (present.returncode, present.stderr) == (0, b"")
    assert b"job-34" in present.stdout  # 존재하는 디렉터리는 그대로 통과한다
    assert (none.returncode, none.stdout, none.stderr) == (0, b"", b"")
    assert (no_root.returncode, no_root.stdout, no_root.stderr) == (0, b"", b"")


def test_pull_images_rejects_a_truncated_corrections_cache_with_the_recovery_step(
    tmp_path, monkeypatch
):
    """폴백 읽기도 `_load_corrections`의 손상 방어를 지난다 — 생 JSONDecodeError는 금지다.

    corrections.json은 비원자 `_write_json`으로 쓰이므로 중단된 fetch가 잘린 파일을 남기는 것이
    현실적 상태다. 그 상태에서 파일명·복구 절차 없는 raw 예외가 나면 report 경로와 진단이 갈린다.
    """
    _write_cache(tmp_path, pairs=[], jobs=[])
    (tmp_path / "corrections.json").write_text('[{"job_id": 5', encoding="utf-8")
    monkeypatch.setattr("tools.curation_report.run_ssh", lambda host, script: b"")
    with pytest.raises(ValueError) as err:
        pull_images("h", "e.env", tmp_path, [5], with_originals=True)
    _assert_names_file_and_recovery(err, "corrections.json")


def test_pull_images_skips_a_job_whose_image_path_is_null(tmp_path, monkeypatch, capsys):
    """image_path가 NULL인 잡은 경고 경로로 보낸다 — `cat 'NULL'`을 원격에 던지지 않는다."""
    _write_cache(
        tmp_path,
        pairs=[],
        jobs=[],
        corrections=[{**_correction(job_id=57), "image_path": None}],
    )
    calls: list[str] = []

    def fake_ssh(host, script):
        calls.append(script)
        return b""

    monkeypatch.setattr("tools.curation_report.run_ssh", fake_ssh)
    pull_images("h", "e.env", tmp_path, [57], with_originals=True)
    assert not any(c.startswith("cat ") for c in calls)
    assert "원본 경로를 찾지 못한 잡(캐시에 없음): [57]" in capsys.readouterr().out


def test_pull_images_skips_a_job_whose_jobs_cache_holds_a_raw_null_path(
    tmp_path, monkeypatch, capsys
):
    """jobs.json은 `--raw` 질의라 SQL NULL이 **문자열** "NULL"로 온다 — 그대로 두면 truthy다.

    corrections.json은 `_cell`이 None으로 접지만 jobs.json이 setdefault 우선이라 폴백이 그 자리를
    회수하지 못한다. 결과는 원격에 나가는 `cat -- NULL`과 원인을 틀리게 적는 경고다.
    """
    _write_cache(
        tmp_path,
        pairs=[],
        jobs=[{"job_id": 57, "image_path": "NULL", "result": {"rows": []}}],
        corrections=[{**_correction(job_id=57), "image_path": None}],
    )
    calls: list[str] = []

    def fake_ssh(host, script):
        calls.append(script)
        return b""

    monkeypatch.setattr("tools.curation_report.run_ssh", fake_ssh)
    pull_images("h", "e.env", tmp_path, [57], with_originals=True)
    assert not any(c.startswith("cat ") for c in calls)
    assert "원본 경로를 찾지 못한 잡(캐시에 없음): [57]" in capsys.readouterr().out


def test_pull_images_quotes_an_original_path_that_needs_quoting(tmp_path, monkeypatch):
    """image_path는 DB 값이지만 원격 셸 문자열에 보간된다 — 인용이 그 계약이다.

    다른 픽스처의 경로는 전부 메타문자가 없어 `shlex.quote`가 no-op이라, 인용을 지우고
    `f"cat -- {image_path}"`로 바꿔도 GREEN이었다(`--` 종결자만 보호되고 있었다).
    """
    _write_cache(
        tmp_path,
        pairs=[],
        jobs=[],
        corrections=[{**_correction(job_id=57), "image_path": "/data/up/57 x;id.jpeg"}],
    )
    calls: list[str] = []

    def fake_ssh(host, script):
        calls.append(script)
        return b"" if "tar -cf -" in script else b"J"

    monkeypatch.setattr("tools.curation_report.run_ssh", fake_ssh)
    pull_images("h", "e.env", tmp_path, [57], with_originals=True)
    # 조립 결과 전문을 고정한다 — 부분 문자열 단언은 인용이 빠진 형태도 통과시킨다.
    assert calls[-1] == "cat -- '/data/up/57 x;id.jpeg'"


def test_pull_images_warns_when_an_original_comes_back_empty(tmp_path, monkeypatch, capsys):
    """0바이트 성공은 빈 original.jpg를 남긴다 — 검수자가 빈 파일을 사진으로 오인한다."""
    _write_cache(tmp_path, pairs=[], jobs=[], corrections=[_correction(job_id=57)])
    monkeypatch.setattr("tools.curation_report.run_ssh", lambda host, script: b"")
    pull_images("h", "e.env", tmp_path, [57], with_originals=True)
    assert "원본이 0바이트인 잡 57(/data/up/57.jpeg)" in capsys.readouterr().out


def test_pull_images_does_not_count_a_zero_byte_original_as_saved(tmp_path, monkeypatch):
    """0바이트는 회수가 아니다 — 세면 요약 줄이 "원본 1/1 회수 · 0건 실패"로 성공을 단언한다.

    그 오독을 막으려고 `PullResult`가 건수를 싣는다(소비자는 꼬리 줄만 읽는 LLM 에이전트다).
    빈 파일도 남기지 않는다 — 경고만으로는 original.jpg가 그대로 남아 사진으로 오인된다.
    """
    _write_cache(tmp_path, pairs=[], jobs=[], corrections=[_correction(job_id=57)])
    monkeypatch.setattr("tools.curation_report.run_ssh", lambda host, script: b"")
    result = pull_images("h", "e.env", tmp_path, [57], with_originals=True)
    assert (result.saved, result.failed) == (0, 1)
    assert not (result.out_dir / "job-57" / "original.jpg").exists()


def test_pull_images_command_reports_how_many_originals_it_saved(tmp_path, monkeypatch, capsys):
    """전면 실패도 성공 문구로 끝나면 안 된다 — 요약 줄(이 도구의 소비자는 LLM이다)이 성패를 싣는다."""
    _write_cache(
        tmp_path, pairs=[], jobs=[], corrections=[_correction(job_id=57), _correction(job_id=58)]
    )

    def fake_ssh(host, script):
        if "tar -cf -" in script:
            return b""
        if "57.jpeg" in script:
            raise RemoteError("ssh 실패(h, exit 1): cat: No such file")
        return b"J"

    monkeypatch.setattr("tools.curation_report.run_ssh", fake_ssh)
    main(["--cache", str(tmp_path), "pull-images", "--jobs", "57", "58", "--originals"])
    assert "(잡 2개, 원본 1/2 회수 · 1건 실패)" in capsys.readouterr().out


def test_pull_images_command_omits_the_original_counts_when_none_were_asked_for(
    tmp_path, monkeypatch, capsys
):
    """--originals 없이 도는 기본 경로에 0/N 같은 없는 실패를 지어내지 않는다."""
    _write_cache(tmp_path, pairs=[], jobs=[], corrections=[_correction(job_id=57)])
    monkeypatch.setattr("tools.curation_report.run_ssh", lambda host, script: b"")
    main(["--cache", str(tmp_path), "pull-images", "--jobs", "57"])
    out = capsys.readouterr().out
    assert "(잡 1개)" in out
    assert "원본" not in out.split("이미지 동기화")[1].splitlines()[0]
