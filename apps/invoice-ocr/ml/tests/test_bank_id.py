"""handwriting.bank_id — retrieval 지문의 순수 계층 단위테스트(numpy는 bank_rows에만)."""

import hashlib
import shutil
import subprocess

import numpy as np
import pytest

from handwriting.bank_id import (
    FINGERPRINT_LEN,
    MODEL_FILENAME,
    bank_retrieval_version,
    bank_rows,
    code_version,
    compute_retrieval_version,
    file_digest,
    retrieval_fingerprint,
)

ROWS = [("job-1/row-0", "엔진오일", b"\x01\x02"), ("job-2/row-0", "타이어", b"\x03\x04")]
DIGEST = "model-a"
CODE = "abc1234"


def _fp(rows=None, model_digest=DIGEST, code=CODE):
    return retrieval_fingerprint(rows if rows is not None else ROWS, model_digest, code)


def test_fingerprint_is_short_hex():
    fp = _fp()
    assert len(fp) == FINGERPRINT_LEN
    assert all(c in "0123456789abcdef" for c in fp)


def test_row_order_does_not_change_the_fingerprint():
    # 행 단위 정규화 — 내용이 같고 순서만 다른 뱅크는 같은 retrieval 상태다.
    assert _fp(list(reversed(ROWS))) == _fp()


def test_label_change_changes_the_fingerprint():
    assert _fp([(ROWS[0][0], "드라이", ROWS[0][2]), ROWS[1]]) != _fp()


def test_key_change_changes_the_fingerprint():
    assert _fp([("job-9/row-0", ROWS[0][1], ROWS[0][2]), ROWS[1]]) != _fp()


def test_emb_row_bytes_change_the_fingerprint_even_when_keys_and_labels_match():
    # bank_update apply는 같은 crop_ref를 현재 모델로 다시 임베딩한다 — keys/labs가 그대로여도
    # emb가 바뀌면 retrieval 결과가 달라진다.
    assert _fp([(ROWS[0][0], ROWS[0][1], b"\x09\x09"), ROWS[1]]) != _fp()


def test_model_digest_change_changes_the_fingerprint():
    # ft_prod.pt만 교체하면 bank.npz는 바이트 단위로 동일한데 쿼리 임베딩이 다른 모델에서 나온다.
    assert _fp(model_digest="model-b") != _fp()


def test_code_version_change_changes_the_fingerprint():
    # 파일이 하나도 안 바뀌고 코드만 배포돼도 전처리·후보 선택이 달라진다(deploy.yml이 워커를 재시작).
    assert _fp(code="def5678") != _fp()


def test_broken_key_to_emb_pairing_changes_the_fingerprint():
    # key 집합·emb 집합은 같고 짝만 뒤바뀐 상태 — 행 대응이 깨진 사고가 지문에 드러나야 한다.
    swapped = [(ROWS[0][0], ROWS[0][1], ROWS[1][2]), (ROWS[1][0], ROWS[1][1], ROWS[0][2])]
    assert _fp(swapped) != _fp()


def test_duplicate_key_is_rejected():
    with pytest.raises(ValueError, match="중복"):
        _fp([ROWS[0], (ROWS[0][0], "다른라벨", b"\x05")])


def test_empty_model_digest_is_rejected():
    # 코드 상태를 모르는데 지문이 나오는 fail-open 경로를 막는다(M1).
    with pytest.raises(ValueError, match="model_digest"):
        retrieval_fingerprint(ROWS, "", CODE)


def test_empty_code_version_is_rejected():
    with pytest.raises(ValueError, match="code_version"):
        retrieval_fingerprint(ROWS, DIGEST, "")


def test_length_prefix_separates_field_boundaries():
    # 구분자 없이 이어 붙이면 ("ab","c")와 ("a","bc")가 같은 바이트가 된다.
    assert _fp([("ab", "c", b"")]) != _fp([("a", "bc", b"")])


def test_bank_rows_pairs_arrays_and_rejects_length_mismatch():
    emb = np.array([[1.0, 2.0], [3.0, 4.0]], dtype="float32")
    rows = bank_rows(["k0", "k1"], ["a", "b"], emb)
    assert [(k, lb) for k, lb, _ in rows] == [("k0", "a"), ("k1", "b")]
    assert rows[0][2] == emb[0].tobytes()
    with pytest.raises(ValueError):
        bank_rows(["k0"], ["a", "b"], emb)


def test_file_digest_streams_the_file(tmp_path):
    p = tmp_path / MODEL_FILENAME
    p.write_bytes(b"weights")
    first = file_digest(p)
    assert first == file_digest(p)
    p.write_bytes(b"weights2")
    assert file_digest(p) != first


def test_file_digest_hashes_every_chunk_not_just_the_first(tmp_path, monkeypatch):
    # 실제 ft_prod.pt(347MB)는 청크 루프를 수백 번 돌지만 테스트 파일은 한 청크에 다 들어가
    # 루프가 1회뿐이다 — "첫 청크만 해시" 회귀가 그대로 통과한다. _CHUNK를 낮춰 다중 청크
    # 경로를 실제로 태우고, 표준 sha256과 대조해 청크 경계 처리까지 고정한다.
    monkeypatch.setattr("handwriting.bank_id._CHUNK", 4)
    data = b"0123456789ab"
    p = tmp_path / MODEL_FILENAME
    p.write_bytes(data)
    assert file_digest(p) == hashlib.sha256(data).hexdigest()


def test_code_version_returns_none_when_git_is_unavailable(monkeypatch):
    def _boom(*a, **kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert code_version() is None


def test_code_version_returns_none_outside_a_repo(tmp_path):
    assert code_version(tmp_path) is None


def _hermetic_repo(path):
    """커밋 1개짜리 레포를 만든다 — 성공 경로를 실행 트리 상태에 의존시키지 않는다.

    ML_ROOT로 성공 경로를 검증하면 tarball·컨테이너처럼 .git이 없는 checkout에서 테스트가
    거짓 실패한다(테스트가 코드 대신 실행 환경을 검증하게 된다). gpgsign·user 설정은 전역
    git config를 타지 않게 -c로 고정한다.
    """
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    path.mkdir(parents=True, exist_ok=True)

    def _git(*args):
        subprocess.run(["git", *args], cwd=str(path), check=True, capture_output=True)

    _git("init", "-q")
    _git(
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=t",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "fixture",
    )
    return path


def test_code_version_returns_full_sha_for_a_git_repo(tmp_path):
    # 성공 경로가 지금까지 어떤 테스트에도 걸려 있지 않았다(H1a).
    version = code_version(_hermetic_repo(tmp_path))
    assert version is not None
    assert len(version) == 40
    assert all(c in "0123456789abcdef" for c in version)


def test_code_version_ignores_inherited_git_env_vars(tmp_path, monkeypatch):
    # GIT_DIR/GIT_WORK_TREE는 cwd보다 우선한다 — 걷어내지 않으면 rev-parse가 repo_dir이 아닌
    # 다른 레포(또는 존재하지 않는 레포)를 보고, 그 SHA도 정상 40자 hex라 검증을 통과해
    # 지문이 조용히 어긋난다.
    repo = _hermetic_repo(tmp_path / "repo")
    expected = code_version(repo)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nonexistent.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "nonexistent"))
    assert code_version(repo) == expected


def test_code_version_logs_reason_to_stderr_when_git_is_unavailable(monkeypatch, capsys):
    # 실패 사유가 로그 한 줄도 없으면 운영에서 retrieval_version 소실 원인을 알 창구가 없다(H1b).
    def _boom(*a, **kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert code_version() is None
    assert capsys.readouterr().err.strip() != ""


def test_code_version_reports_the_git_returncode_when_rev_parse_fails(monkeypatch, capsys):
    # returncode 분기를 지워도 stdout이 비어 SHA 형식 검증에 걸려 None이 나온다 — 즉
    # "None을 돌려준다"만 단언하면 이 분기가 고정되지 않는다. 진단에 git의 returncode와
    # stderr가 실려야 운영에서 원인(권한·손상·detached 아님)을 로그만으로 좁힐 수 있다.
    class _FakeProc:
        returncode = 128
        stdout = b""
        stderr = b"fatal: not a git repository"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeProc())
    assert code_version() is None
    err = capsys.readouterr().err
    assert "returncode=128" in err
    assert "not a git repository" in err


def test_code_version_returns_none_when_git_hangs(monkeypatch, capsys):
    # 워커는 기동 중 이 함수를 부른다 — timeout이 없으면 git이 매달릴 때 워커 기동이
    # 무한 블록된다(운영 중단). timeout 인자 자체를 단언해야 그 회귀가 잡힌다.
    seen = {}

    def _hang(*a, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd="git", timeout=kw["timeout"])

    monkeypatch.setattr(subprocess, "run", _hang)
    assert code_version() is None
    assert seen["timeout"] > 0
    assert capsys.readouterr().err.strip() != ""


def test_code_version_returns_none_for_malformed_git_output(monkeypatch, capsys):
    # git stdout을 형식 검증 없이 신뢰하면 비UTF-8 잡음도 지문 입력이 된다(M2).
    class _FakeProc:
        returncode = 0
        stdout = b"not-a-sha\n"
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeProc())
    assert code_version() is None
    assert capsys.readouterr().err.strip() != ""


def test_compute_retrieval_version_is_none_when_code_sha_is_missing(tmp_path, monkeypatch):
    # SHA 없이 자리표시자를 넣으면 서로 다른 코드 상태가 한 지문으로 합쳐지는 fail-open이 된다.
    model = tmp_path / "ft_prod.pt"
    model.write_bytes(b"w")
    monkeypatch.setattr("handwriting.bank_id.code_version", lambda repo_dir=None: None)
    emb = np.zeros((1, 2), dtype="float32")
    assert compute_retrieval_version(model, ["k0"], ["a"], emb) is None


def test_bank_retrieval_version_owns_the_model_filename_and_array_selection(tmp_path, monkeypatch):
    """M4 — 지문 '입력'(모델 파일명·keys/emb 선택)의 단일 진입점.

    해시 로직만 공유하고 입력을 호출부마다 복붙하면(워커 vs 원격 스크립트) 파일명이나 배열
    선택이 한쪽만 바뀔 때 지문이 전량 어긋나 모든 잡이 조용히 stale이 된다.
    """
    model = tmp_path / MODEL_FILENAME
    model.write_bytes(b"w")
    monkeypatch.setattr("handwriting.bank_id.code_version", lambda repo_dir=None: "sha1")
    emb = np.zeros((1, 2), dtype="float32")
    npz = {"keys": np.array(["k0"], dtype=object), "emb": emb}

    assert bank_retrieval_version(tmp_path, npz, ["a"]) == compute_retrieval_version(
        model, ["k0"], ["a"], emb
    )


def test_bank_retrieval_version_accepts_a_str_models_dir(tmp_path, monkeypatch):
    """실제 소비자 하나는 str을 넘긴다 — tools/curation_report.py의 원격 인라인 스크립트가
    os.environ['SJMJ_ML_MODELS_DIR']를 그대로 전달한다. Path 강제 변환이 사라지면 워커
    경로만 멀쩡하고 원격만 TypeError로 죽어 자기 except에 삼켜진다(지문 전량 None).
    """
    (tmp_path / MODEL_FILENAME).write_bytes(b"w")
    monkeypatch.setattr("handwriting.bank_id.code_version", lambda repo_dir=None: "sha1")
    npz = {"keys": np.array(["k0"], dtype=object), "emb": np.zeros((1, 2), dtype="float32")}

    from_str = bank_retrieval_version(str(tmp_path), npz, ["a"])
    assert from_str is not None
    assert from_str == bank_retrieval_version(tmp_path, npz, ["a"])


def test_bank_retrieval_version_propagates_a_bank_without_keys(tmp_path):
    """실패를 삼키지 않는다 — fail-safe는 호출자(운영 워커·원격 스크립트)의 경계다."""
    (tmp_path / MODEL_FILENAME).write_bytes(b"w")
    with pytest.raises(KeyError):
        bank_retrieval_version(tmp_path, {"emb": np.zeros((1, 2), dtype="float32")}, ["a"])


def test_compute_retrieval_version_tracks_the_model_file_bytes(tmp_path, monkeypatch):
    model = tmp_path / "ft_prod.pt"
    model.write_bytes(b"w1")
    monkeypatch.setattr("handwriting.bank_id.code_version", lambda repo_dir=None: "sha1")
    emb = np.zeros((1, 2), dtype="float32")
    first = compute_retrieval_version(model, ["k0"], ["a"], emb)
    model.write_bytes(b"w2")
    second = compute_retrieval_version(model, ["k0"], ["a"], emb)
    assert first and second and first != second
