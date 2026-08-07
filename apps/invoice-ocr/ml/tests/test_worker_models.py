"""worker.main의 모델 번들 계약과 지문 fail-safe (실모델·torch 비의존)."""

import sys
import types

import numpy as np
import pytest

import handwriting
import worker.main as main_mod
from worker.main import ModelBundle, load_models, retrieval_version_or_none
from worker.poll import DegenerateWorkerState, PollOutcome


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


def test_worker_loads_the_same_artifact_files_the_fingerprint_uses():
    """M4 — 워커는 bank_id를 fail-safe 밖에서 import할 수 없어(기동을 깨면 안 된다) 파일명을
    상수 참조로 공유하지 못한다. 리터럴이 갈라지면 워커가 추론에 쓴 파일과 지문이 해시·집계하는
    파일이 달라져(원격 분석 스크립트는 bank_id 상수를 쓴다) 모든 잡이 조용히 stale이 된다.
    """
    from handwriting import bank_id
    from worker.main import BANK_FILENAME, MODEL_FILENAME

    assert MODEL_FILENAME == bank_id.MODEL_FILENAME
    assert BANK_FILENAME == bank_id.BANK_FILENAME


def _fake_npz() -> dict:
    return {
        "emb": np.ones((2, 3), dtype="float32"),
        "lab": np.array(["가", "나"], dtype=object),
        "keys": np.array(["k1", "k2"], dtype=object),
    }


def _install_fake_bank(monkeypatch, tmp_path, *, compute_retrieval_version, npz=None) -> dict:
    """load_models가 실제로 실행되도록 torch 의존 handwriting.infer_photo와 np.load를 가짜로 교체.

    handwriting.infer_photo는 모듈 최상단에서 torch를 import해 이 venv(worker+cv)에는 없다
    (tests/test_infer_job_gate.py와 동일 사유·동일 패턴). 그래서 그 모듈만 가짜로 갈아끼우고
    np.load만 합성 뱅크로 바꿔, load_models 본문(속성 읽기·인자 순서)은 실제로 실행한다.

    Returns:
        가짜 np.load가 받은 경로를 `bank_path`로 담는 dict — "어느 뱅크 파일을 여는가"를
        호출부가 단언할 수 있게 한다(가짜가 인자를 무시하면 파일명 회귀가 통과한다).
    """
    fake_infer_photo = types.ModuleType("handwriting.infer_photo")
    fake_infer_photo.load_model_from = lambda path, device: f"model:{path.name}:{device}"
    fake_infer_photo.load_ocr = lambda: "qwen-stub"
    monkeypatch.setattr(handwriting, "infer_photo", fake_infer_photo, raising=False)
    monkeypatch.setitem(sys.modules, "handwriting.infer_photo", fake_infer_photo)

    loaded = _fake_npz() if npz is None else npz
    seen: dict = {}

    def fake_load(path, *a, **kw):
        seen["bank_path"] = path
        return loaded

    monkeypatch.setattr("numpy.load", fake_load)
    monkeypatch.setattr("handwriting.bank_id.compute_retrieval_version", compute_retrieval_version)
    monkeypatch.setenv("SJMJ_ML_MODELS_DIR", str(tmp_path))
    return seen


def test_load_models_wires_the_fingerprint_through(monkeypatch, tmp_path):
    """load_models가 z["keys"]·z["emb"]·labs를 뒤섞지 않고 지문 계산에 넘기는지 확인한다.

    인자는 기록만 하고 단언은 fail-safe(retrieval_version_or_none의 except) 밖에서 한다 —
    안에서 assert하면 AssertionError가 그 except에 삼켜져 진단이 "지문 계산 실패" 한 줄로
    뭉개진다. 인자 순서 회귀(예: z↔labs 교환)는 여기서 실패로 드러나야 한다.
    """
    from handwriting import bank_id

    seen: dict = {}

    def fake_compute(model_path, keys, labs, emb):
        seen.update(model_path=model_path, keys=keys, labs=labs, emb=emb)
        return "fingerprint123"

    files = _install_fake_bank(monkeypatch, tmp_path, compute_retrieval_version=fake_compute)

    bundle = load_models()

    assert bundle.retrieval_version == "fingerprint123"
    assert bundle.labs == ["가", "나"]
    assert bundle.device == "cpu"
    # 지문이 추론과 같은 파일을 쓰는지 — 리터럴이 아니라 bank_id 상수와 대조한다.
    assert files["bank_path"] == tmp_path / bank_id.BANK_FILENAME
    assert seen["model_path"] == tmp_path / bank_id.MODEL_FILENAME
    assert seen["keys"] == ["k1", "k2"]
    assert seen["labs"] == ["가", "나"]
    assert seen["emb"].shape == (2, 3)


@pytest.mark.parametrize("missing", ["emb", "lab"])
def test_load_models_hard_fails_when_the_bank_lacks_inference_arrays(
    monkeypatch, tmp_path, missing
):
    """추론 필수 자원은 fail-safe가 아니다 — 뱅크 없는 워커가 조용히 기동하면 안 된다.

    지문(keys·코드 SHA)의 실패는 진단 필드 하나의 손실이라 삼키지만, emb/lab이 없으면 품목
    retrieval 자체가 불가능하다. 그때 조용히 기동하면 전 잡이 쓰레기 초안을 내고 launchd는
    "정상"으로 보고한다. 광범위 except가 이 적재까지 덮거나 z.get()으로 완화되면 여기서 깨진다.
    """
    npz = {k: v for k, v in _fake_npz().items() if k != missing}
    _install_fake_bank(
        monkeypatch,
        tmp_path,
        compute_retrieval_version=lambda *a, **kw: "fingerprint123",
        npz=npz,
    )

    with pytest.raises(KeyError):
        load_models()


def test_load_models_logs_the_boot_fingerprint_to_stderr(monkeypatch, tmp_path, capsys):
    # 로그↔DB 대조로 스탬프 소실을 즉시 알 수 있어야 한다 — 부팅 성공 시에도 지문 한 줄을 남긴다.
    _install_fake_bank(
        monkeypatch, tmp_path, compute_retrieval_version=lambda *a, **kw: "fingerprint123"
    )

    load_models()

    assert "fingerprint123" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# main() 루프 — 크래시루프 카운터 소유 (이슈 #99, spec §3)
# ---------------------------------------------------------------------------


def _run_main_with(monkeypatch, tmp_path, outcomes):
    """main()을 outcomes만큼 돌리고 각 호출이 받은 qwen_jobs_before를 기록한다.

    outcomes가 소진되면 SystemExit(0)으로 무한 루프를 끊는다 — 실 워커의 종료 조건은 없다.
    """
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main_mod, "build_engine", lambda: object())
    monkeypatch.setattr(main_mod, "WorkerQueue", lambda engine: object())
    monkeypatch.setattr(main_mod, "load_models", lambda: object())
    monkeypatch.setattr(main_mod.time, "sleep", lambda *_a: None)
    seen = []
    it = iter(outcomes)

    def fake_process(queue, infer_fn, crops_root, qwen_jobs_before):
        seen.append(qwen_jobs_before)
        try:
            return next(it)
        except StopIteration:
            raise SystemExit(0) from None

    monkeypatch.setattr(main_mod, "process_one_job", fake_process)
    return seen


def test_main_counts_only_the_jobs_that_actually_called_qwen(monkeypatch, tmp_path):
    # 게이트 강등(False) → 정상(True) → 정상(True) → 큐 빔(False)
    outcomes = [
        PollOutcome(worked=True, qwen_called=False),
        PollOutcome(worked=True, qwen_called=True),
        PollOutcome(worked=True, qwen_called=True),
        PollOutcome(worked=False, qwen_called=False),
    ]
    seen = _run_main_with(monkeypatch, tmp_path, outcomes)

    with pytest.raises(SystemExit):
        main_mod.main()

    # outcomes 소진 뒤 루프를 끊는 5번째 호출도 seen에 남으므로 앞 4건만 본다.
    assert seen[:4] == [0, 0, 1, 2], "qwen_called일 때만, 그리고 즉시 증가해야 한다"


def test_main_does_not_swallow_the_degenerate_worker_state(monkeypatch, tmp_path):
    """프로세스 비0 종료가 곧 복구 수단이다 — 루프가 이 예외를 삼키면 재기동이 없다."""
    _run_main_with(monkeypatch, tmp_path, [])

    def always_degenerate(queue, infer_fn, crops_root, qwen_jobs_before):
        raise DegenerateWorkerState(1)

    monkeypatch.setattr(main_mod, "process_one_job", always_degenerate)

    with pytest.raises(DegenerateWorkerState) as exc:
        main_mod.main()

    assert exc.value.code == 1
