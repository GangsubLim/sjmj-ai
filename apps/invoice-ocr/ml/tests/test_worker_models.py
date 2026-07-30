"""worker.main의 모델 번들 계약과 지문 fail-safe (실모델·torch 비의존)."""

import sys
import types

import numpy as np

import handwriting
from worker.main import ModelBundle, load_models, retrieval_version_or_none


def test_model_bundle_field_order_is_pinned():
    # infer_job이 속성으로 읽으므로 순서 실수는 조용히 통과하지 않지만, 필드 이름 자체가
    # worker↔handwriting 계약이라 여기서 고정한다.
    assert ModelBundle._fields == (
        "item_model",
        "emb",
        "labs",
        "qwen",
        "device",
        "retrieval_version",
    )


def test_model_bundle_defaults_retrieval_version_to_none():
    b = ModelBundle("m", np.zeros((1, 2), dtype="float32"), ["a"], "q", "cpu")
    assert b.retrieval_version is None


def test_retrieval_version_or_none_returns_the_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "handwriting.bank_id.compute_retrieval_version",
        lambda *a, **kw: "a1b2c3d4e5f6",
    )
    npz = {"keys": ["k"], "emb": None}
    assert retrieval_version_or_none(tmp_path, npz, ["a"]) == "a1b2c3d4e5f6"


def test_retrieval_version_or_none_swallows_failures_to_keep_the_worker_booting(
    tmp_path, monkeypatch, capsys
):
    # 운영 중단이 스탬프보다 비싸다 — 지문 계산 실패는 기동을 실패시키지 않고 키를 생략한다.
    def _boom(*a, **kw):
        raise RuntimeError("뱅크 key 중복")

    monkeypatch.setattr("handwriting.bank_id.compute_retrieval_version", _boom)
    npz = {"keys": ["k"], "emb": None}
    assert retrieval_version_or_none(tmp_path, npz, ["a"]) is None
    assert "retrieval-version" in capsys.readouterr().err


def test_retrieval_version_or_none_survives_a_bank_without_keys(tmp_path, capsys):
    """keys 없는 뱅크로도 워커는 기동한다 — 현행 워커는 emb/lab만 요구한다(load_models).

    compute_retrieval_version을 monkeypatch하지 않는다 — 그러면 z["keys"] KeyError 경로가
    커버되지 않는다(fail-safe가 실제 실패원을 덮는지 확인하는 것이 이 테스트의 목적이다).
    """
    npz = {"emb": np.ones((1, 2), dtype="float32"), "lab": np.array(["a"], object)}

    class _NoKeys(dict):
        def __getitem__(self, k):
            if k == "keys":
                raise KeyError("keys")
            return dict.__getitem__(self, k)

    assert retrieval_version_or_none(tmp_path, _NoKeys(npz), ["a"]) is None
    assert "retrieval-version" in capsys.readouterr().err


def test_retrieval_version_or_none_delegates_to_the_shared_entry_point(tmp_path, monkeypatch):
    """M4 — 워커도 원격 분석 스크립트도 지문 '입력'을 각자 고르지 않고 한 함수를 부른다.

    파일명·배열 선택이 한쪽만 바뀌면 두 지문이 어긋나 모든 잡이 조용히 stale이 된다.
    """
    seen = {}

    def fake(models_dir, npz, labs):
        seen.update(models_dir=models_dir, labs=labs)
        return "fp123456789a"

    monkeypatch.setattr("handwriting.bank_id.bank_retrieval_version", fake)
    assert retrieval_version_or_none(tmp_path, {"emb": None}, ["가"]) == "fp123456789a"
    assert seen == {"models_dir": tmp_path, "labs": ["가"]}


def test_worker_loads_the_same_model_file_the_fingerprint_hashes():
    """M4 — 워커는 bank_id를 fail-safe 밖에서 import할 수 없어(기동을 깨면 안 된다) 파일명을
    상수 참조로 공유하지 못한다. 두 리터럴이 갈라지면 지문이 추론과 다른 파일을 해시한다.
    """
    from handwriting import bank_id
    from worker.main import MODEL_FILENAME

    assert MODEL_FILENAME == bank_id.MODEL_FILENAME


def _install_fake_bank(monkeypatch, tmp_path, *, compute_retrieval_version):
    """load_models가 실제로 실행되도록 torch 의존 handwriting.infer_photo와 np.load를 가짜로 교체.

    handwriting.infer_photo는 모듈 최상단에서 torch를 import해 이 venv(worker+cv)에는 없다
    (tests/test_infer_job_gate.py와 동일 사유·동일 패턴). 그래서 그 모듈만 가짜로 갈아끼우고
    np.load만 합성 뱅크로 바꿔, load_models 본문(속성 읽기·인자 순서)은 실제로 실행한다.
    """
    fake_infer_photo = types.ModuleType("handwriting.infer_photo")
    fake_infer_photo.load_model_from = lambda path, device: f"model:{path.name}:{device}"
    fake_infer_photo.load_ocr = lambda: "qwen-stub"
    monkeypatch.setattr(handwriting, "infer_photo", fake_infer_photo, raising=False)
    monkeypatch.setitem(sys.modules, "handwriting.infer_photo", fake_infer_photo)

    fake_npz = {
        "emb": np.ones((2, 3), dtype="float32"),
        "lab": np.array(["가", "나"], dtype=object),
        "keys": np.array(["k1", "k2"], dtype=object),
    }
    monkeypatch.setattr("numpy.load", lambda *a, **kw: fake_npz)
    monkeypatch.setattr("handwriting.bank_id.compute_retrieval_version", compute_retrieval_version)
    monkeypatch.setenv("SJMJ_ML_MODELS_DIR", str(tmp_path))


def test_load_models_wires_the_fingerprint_through(monkeypatch, tmp_path):
    """load_models가 z["keys"]·z["emb"]·labs를 뒤섞지 않고 지문 계산에 넘기는지 확인한다.

    compute_retrieval_version을 인자별로 검증하는 가짜로 바꿔, 인자 순서 회귀(예:
    z↔labs 교환)가 나면 fail-safe가 조용히 None을 반환하는 대신 이 테스트가 실패한다.
    """

    def fake_compute(model_path, keys, labs, emb):
        assert model_path.name == "ft_prod.pt"
        assert keys == ["k1", "k2"]
        assert labs == ["가", "나"]
        assert emb.shape == (2, 3)
        return "fingerprint123"

    _install_fake_bank(monkeypatch, tmp_path, compute_retrieval_version=fake_compute)

    bundle = load_models()

    assert bundle.retrieval_version == "fingerprint123"
    assert bundle.labs == ["가", "나"]
    assert bundle.device == "cpu"


def test_load_models_logs_the_boot_fingerprint_to_stderr(monkeypatch, tmp_path, capsys):
    # 로그↔DB 대조로 스탬프 소실을 즉시 알 수 있어야 한다 — 부팅 성공 시에도 지문 한 줄을 남긴다.
    _install_fake_bank(
        monkeypatch, tmp_path, compute_retrieval_version=lambda *a, **kw: "fingerprint123"
    )

    load_models()

    assert "fingerprint123" in capsys.readouterr().err
