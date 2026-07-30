"""handwriting.bank_id — retrieval 지문의 순수 계층 단위테스트(numpy는 bank_rows에만)."""

import shutil
import subprocess

import numpy as np
import pytest

from handwriting.bank_id import (
    FINGERPRINT_LEN,
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
    p = tmp_path / "ft_prod.pt"
    p.write_bytes(b"weights")
    first = file_digest(p)
    assert first == file_digest(p)
    p.write_bytes(b"weights2")
    assert file_digest(p) != first


def test_code_version_returns_none_when_git_is_unavailable(monkeypatch):
    def _boom(*a, **kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert code_version() is None


def test_code_version_returns_none_outside_a_repo(tmp_path):
    assert code_version(tmp_path) is None


def test_code_version_returns_full_sha_in_a_real_git_checkout():
    # ml CI 잡은 실제 git checkout에서 돈다 — 이 성공 경로가 지금까지 어떤 테스트에도
    # 걸려 있지 않았다(H1a). git이 없는 환경만 skip한다.
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    version = code_version()
    assert version is not None
    assert len(version) == 40
    assert all(c in "0123456789abcdef" for c in version)


def test_code_version_logs_reason_to_stderr_when_git_is_unavailable(monkeypatch, capsys):
    # 실패 사유가 로그 한 줄도 없으면 운영에서 retrieval_version 소실 원인을 알 창구가 없다(H1b).
    def _boom(*a, **kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert code_version() is None
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


def test_compute_retrieval_version_tracks_the_model_file_bytes(tmp_path, monkeypatch):
    model = tmp_path / "ft_prod.pt"
    model.write_bytes(b"w1")
    monkeypatch.setattr("handwriting.bank_id.code_version", lambda repo_dir=None: "sha1")
    emb = np.zeros((1, 2), dtype="float32")
    first = compute_retrieval_version(model, ["k0"], ["a"], emb)
    model.write_bytes(b"w2")
    second = compute_retrieval_version(model, ["k0"], ["a"], emb)
    assert first and second and first != second
