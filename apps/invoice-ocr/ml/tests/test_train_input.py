"""tools.train_input 단위테스트 (torch·실데이터 비의존 — 합성 npz + 임시 PNG만)."""

import sys
import types
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import import_scopes
from tools.train_input import (
    BOOTSTRAP_ARRAYS,
    TrainSet,
    load_bootstrap,
    load_curated,
    merge,
    select_curated,
)

SRC = Path(__file__).resolve().parents[1] / "tools" / "train_input.py"


def _npz(path, n=3, drop=(), sq=None):
    """부트스트랩 npz를 만든다 — 실파일과 같이 다섯 번째 배열 `key`를 포함한다."""
    arrays = {
        "sq": np.zeros((n, 224, 224, 3), np.uint8) if sq is None else sq,
        "lab": np.array([f"품목{i}" for i in range(n)], object),
        "inv": np.array([f"2025-08-18_inv{i:03d}.jpg" for i in range(n)], object),
        "keys": np.array([f"2025-08-18_inv{i:03d}_0" for i in range(n)], object),
        "key": "cachekey01",
    }
    for k in drop:
        del arrays[k]
    np.savez(path, **arrays)
    return path


def _pair(**over):
    base = {
        "id": 1,
        "crop_ref": "job-1/row-0",
        "job_id": 1,
        "row_index": 0,
        "draft_label": "엔진오일",
        "final_label": "엔진오일",
        "canonical_label": "엔진오일",
        "supply": 100000,
        "status": "included",
        "exclusion_reason": None,
        "reviewed_at": None,
    }
    return {**base, **over}


def _png(root, ref):
    """crop_ref에 대응하는 실 PNG를 쓴다(cv2.imread가 읽을 수 있어야 한다)."""
    import cv2

    path = Path(root) / f"{ref}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((40, 90, 3), 255, np.uint8))
    return path


def _fake_square(img):
    return np.zeros((224, 224, 3), np.uint8)


# --- 부트스트랩 적재 ---


def test_load_bootstrap_reads_the_four_arrays_and_tags_every_item_bootstrap(tmp_path):
    ts = load_bootstrap(_npz(tmp_path / "clean_crops.npz", n=3))

    assert ts.keys == ("2025-08-18_inv000_0", "2025-08-18_inv001_0", "2025-08-18_inv002_0")
    assert ts.inv == ("2025-08-18_inv000.jpg", "2025-08-18_inv001.jpg", "2025-08-18_inv002.jpg")
    assert ts.lab == ("품목0", "품목1", "품목2")
    assert ts.origin == ("bootstrap", "bootstrap", "bootstrap")
    assert len(ts.sq) == 3 and ts.sq[0].shape == (224, 224, 3)


def test_load_bootstrap_tolerates_the_extra_cache_key_entry(tmp_path):
    """실파일에는 prepare()가 남긴 `key`(md5) 배열이 하나 더 있다 — 상등 검증이면 실파일이 죽는다."""
    z = np.load(_npz(tmp_path / "c.npz", n=2), allow_pickle=True)
    assert "key" in z.files and set(BOOTSTRAP_ARRAYS) < set(z.files)

    assert len(load_bootstrap(tmp_path / "c.npz").keys) == 2


def test_load_bootstrap_fails_fast_and_names_the_missing_array(tmp_path):
    with pytest.raises(ValueError, match="inv"):
        load_bootstrap(_npz(tmp_path / "c.npz", n=2, drop=("inv",)))


def test_load_bootstrap_rejects_a_crop_array_that_is_not_224_rgb(tmp_path):
    bad = np.zeros((2, 128, 128, 3), np.uint8)
    with pytest.raises(ValueError, match="224"):
        load_bootstrap(_npz(tmp_path / "c.npz", n=2, sq=bad))


# --- 큐레이션 모집단 ---


def test_select_curated_applies_the_same_gate_as_bank_update(tmp_path):
    """모집단 규칙은 재구현이 아니라 bank_update 호출이어야 한다(ADR 0004 단일화)."""
    from tools.bank_update import partition_crop_ref, partition_valid, select_desired

    pairs = [
        _pair(id=1, job_id=1, crop_ref="job-1/row-0"),
        _pair(id=2, job_id=9, crop_ref="job-9/row-0"),  # 미검수 잡
        _pair(id=3, job_id=1, crop_ref="job-1/row-1", status="excluded"),
        _pair(id=4, job_id=1, crop_ref="job-1/row-2", canonical_label="  "),  # 라벨 무효
        _pair(id=5, job_id=1, crop_ref="legacy_key"),  # crop_ref 형식 불량
    ]
    reviewed = {1}

    expected, _ = partition_valid(partition_crop_ref(select_desired(pairs, reviewed))[0])

    assert [p["crop_ref"] for p in select_curated(pairs, reviewed)] == [
        p["crop_ref"] for p in expected
    ]
    assert [p["crop_ref"] for p in select_curated(pairs, reviewed)] == ["job-1/row-0"]


# --- 큐레이션 크롭 적재 ---


def test_load_curated_derives_inv_and_key_from_crop_ref(tmp_path):
    root = tmp_path / "ocr_crops"
    _png(root, "job-42/row-3")
    pairs = [_pair(job_id=42, crop_ref="job-42/row-3", canonical_label="안가방")]

    ts = load_curated(pairs, root, square_fn=_fake_square)

    assert ts.keys == ("job-42/row-3",)
    assert ts.inv == ("job-42",)
    assert ts.lab == ("안가방",)
    assert ts.origin == ("curated",)
    assert ts.sq[0].shape == (224, 224, 3)


def test_load_curated_fails_fast_and_lists_every_missing_png(tmp_path):
    root = tmp_path / "ocr_crops"
    _png(root, "job-1/row-0")
    pairs = [
        _pair(crop_ref="job-1/row-0"),
        _pair(id=2, crop_ref="job-1/row-1"),
        _pair(id=3, job_id=2, crop_ref="job-2/row-0"),
    ]

    with pytest.raises(FileNotFoundError) as exc:
        load_curated(pairs, root, square_fn=_fake_square)

    assert "job-1/row-1" in str(exc.value) and "job-2/row-0" in str(exc.value)


def test_load_curated_rejects_a_square_fn_that_returns_the_wrong_shape(tmp_path):
    root = tmp_path / "ocr_crops"
    _png(root, "job-1/row-0")
    pairs = [_pair(crop_ref="job-1/row-0")]

    with pytest.raises(ValueError, match="job-1/row-0"):
        load_curated(pairs, root, square_fn=lambda img: np.zeros((128, 128, 3), np.uint8))


def test_load_curated_defaults_to_the_production_square_preprocessing(tmp_path, monkeypatch):
    """기본 전처리는 운영 임베딩과 같은 handwriting.fewshot.square다(torch 의존이라 fake 주입)."""
    seen = []
    fake = types.ModuleType("handwriting.fewshot")
    fake.square = lambda img: seen.append(img.shape) or np.zeros((224, 224, 3), np.uint8)
    monkeypatch.setitem(sys.modules, "handwriting.fewshot", fake)
    root = tmp_path / "ocr_crops"
    _png(root, "job-1/row-0")

    load_curated([_pair(crop_ref="job-1/row-0")], root)

    assert seen == [(40, 90, 3)]


# --- 병합 ---


def test_merge_concatenates_and_preserves_origin(tmp_path):
    root = tmp_path / "ocr_crops"
    _png(root, "job-1/row-0")
    boot = load_bootstrap(_npz(tmp_path / "c.npz", n=2))
    cur = load_curated([_pair(crop_ref="job-1/row-0")], root, square_fn=_fake_square)

    ts = merge(boot, cur)

    assert ts.keys == boot.keys + cur.keys
    assert ts.origin == ("bootstrap", "bootstrap", "curated")
    assert len(ts.sq) == 3


def test_merge_rejects_a_duplicate_key(tmp_path):
    boot = load_bootstrap(_npz(tmp_path / "c.npz", n=2))

    with pytest.raises(ValueError, match="2025-08-18_inv000_0"):
        merge(boot, boot)

    # 같은 TrainSet 내부에 중복 key가 있으면 두 벌 병합 전에도 잡아야 한다.
    dup = TrainSet(
        sq=boot.sq[:1] * 2,
        lab=("A", "B"),
        inv=(boot.inv[0], boot.inv[0]),
        keys=(boot.keys[0], boot.keys[0]),
        origin=("bootstrap", "bootstrap"),
    )
    with pytest.raises(ValueError, match=boot.keys[0]):
        merge(dup)


def test_train_set_is_frozen(tmp_path):
    ts = load_bootstrap(_npz(tmp_path / "c.npz", n=1))
    with pytest.raises(FrozenInstanceError):
        ts.lab = ("x",)


# --- 코어 경량 규약 ---


def test_train_input_keeps_heavy_imports_out_of_module_scope():
    """코어는 paddle-free 경량 — numpy/cv2/torch·fewshot은 함수 본문에서만 import한다."""
    module_level, in_functions = import_scopes(SRC)

    assert module_level & {"numpy", "cv2", "torch", "handwriting.fewshot"} == set()
    assert {"numpy", "cv2", "handwriting.fewshot"} <= in_functions
