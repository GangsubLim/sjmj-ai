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
    Cell,
    TrainSet,
    job_folds,
    limit_curated,
    load_bootstrap,
    load_curated,
    merge,
    plan_cells,
    score_cell,
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


def test_load_bootstrap_rejects_columns_whose_lengths_disagree(tmp_path):
    """lab/inv/keys가 sq와 다른 길이로 어긋나면 즉시 실패한다 — 부분 정렬된 npz 방치 금지."""
    path = tmp_path / "c.npz"
    np.savez(
        path,
        sq=np.zeros((3, 224, 224, 3), np.uint8),
        lab=np.array(["a", "b"], object),
        inv=np.array(
            ["2025-08-18_inv000.jpg", "2025-08-18_inv001.jpg", "2025-08-18_inv002.jpg"], object
        ),
        keys=np.array(
            ["2025-08-18_inv000_0", "2025-08-18_inv001_0", "2025-08-18_inv002_0"], object
        ),
        key="cachekey01",
    )

    with pytest.raises(ValueError, match="열 길이 불일치"):
        load_bootstrap(path)


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


def test_load_curated_rejects_a_square_fn_that_returns_the_wrong_dtype(tmp_path):
    """shape는 맞아도 dtype이 uint8이 아니면 거부한다 — 검증이 shape OR dtype 두 갈래다."""
    root = tmp_path / "ocr_crops"
    _png(root, "job-1/row-0")
    pairs = [_pair(crop_ref="job-1/row-0")]

    with pytest.raises(ValueError, match="job-1/row-0"):
        load_curated(pairs, root, square_fn=lambda img: np.zeros((224, 224, 3), np.float32))


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


# --- 실험계획: 잡 단위 K분할 ---

_CUR_KEYS = tuple(f"job-{j}/row-{r}" for j in (1, 2, 3, 4, 5, 6) for r in (0, 1))
_CUR_INVS = tuple(f"job-{j}" for j in (1, 2, 3, 4, 5, 6) for _ in (0, 1))
_CUR_LABS = ("A", "B", "A", "C", "B", "C", "A", "D", "B", "D", "C", "A")
_BOOT_KEYS = ("2025-08-18_inv011_0", "2025-08-18_inv011_1")
_BOOT_LABS = ("A", "E")


def test_job_folds_puts_every_job_in_exactly_one_fold():
    folds = job_folds(_CUR_INVS, 3, 7)

    assert len(folds) == 3
    assert set().union(*folds) == set(_CUR_INVS)
    assert sum(len(f) for f in folds) == len(set(_CUR_INVS))


def test_job_folds_is_reproducible_for_a_seed_and_varies_across_seeds():
    assert job_folds(_CUR_INVS, 3, 7) == job_folds(_CUR_INVS, 3, 7)
    assert job_folds(_CUR_INVS, 3, 7) != job_folds(_CUR_INVS, 3, 8)


def test_job_folds_rejects_a_bootstrap_invoice():
    """부트스트랩 전표는 fold 축이 아니다 — 섞여 들어오면 hold-out 정의가 무너진다."""
    with pytest.raises(ValueError, match="2025-08-18_inv011.jpg"):
        job_folds((*_CUR_INVS, "2025-08-18_inv011.jpg"), 3, 7)


def test_job_folds_rejects_more_folds_than_jobs():
    with pytest.raises(ValueError, match="fold"):
        job_folds(("job-1", "job-2"), 3, 7)


def test_job_folds_allows_k_equal_to_the_number_of_jobs():
    """leave-one-job-out(k == 잡 수)도 유효 경계다 — `k >= len(jobs)`로 바뀌는 off-by-one 차단."""
    jobs = set(_CUR_INVS)
    folds = job_folds(_CUR_INVS, len(jobs), 7)

    assert len(folds) == len(jobs)
    assert all(f for f in folds)
    assert set().union(*folds) == jobs


def test_job_folds_rejects_fewer_than_one_fold():
    with pytest.raises(ValueError, match="fold"):
        job_folds(_CUR_INVS, 0, 7)


# --- 실험계획: N 제한 ---


def test_limit_curated_cuts_at_a_job_boundary_and_never_exceeds_n():
    keep = limit_curated(_CUR_KEYS, _CUR_INVS, 5, 7)

    assert len(keep) <= 5
    assert len(keep) % 2 == 0  # 잡마다 쌍 2건 — 잡 경계에서만 잘린다
    kept_jobs = {k.split("/", 1)[0] for k in keep}
    for job in kept_jobs:
        assert {k for k in _CUR_KEYS if k.startswith(f"{job}/")} <= keep


def test_limit_curated_returns_an_empty_set_for_zero():
    assert limit_curated(_CUR_KEYS, _CUR_INVS, 0, 7) == set()


def test_limit_curated_returns_everything_when_n_reaches_the_total():
    assert limit_curated(_CUR_KEYS, _CUR_INVS, len(_CUR_KEYS), 7) == set(_CUR_KEYS)


def test_limit_curated_is_nested_across_n_so_the_curve_x_axis_is_monotone():
    """작은 N은 큰 N의 부분집합이어야 한다 — 아니면 곡선의 단조성이 데이터 교체로 오염된다."""
    small = limit_curated(_CUR_KEYS, _CUR_INVS, 4, 7)
    large = limit_curated(_CUR_KEYS, _CUR_INVS, 8, 7)

    assert small <= large


def test_limit_curated_is_reproducible_for_a_seed():
    assert limit_curated(_CUR_KEYS, _CUR_INVS, 6, 7) == limit_curated(_CUR_KEYS, _CUR_INVS, 6, 7)


# --- 실험계획: 셀 생성 ---


def _cells(n_grid=(0, 4, 12), k=3, seed=7):
    return plan_cells(
        curated_keys=_CUR_KEYS,
        curated_invs=_CUR_INVS,
        curated_labs=_CUR_LABS,
        bootstrap_keys=_BOOT_KEYS,
        bootstrap_labs=_BOOT_LABS,
        n_grid=n_grid,
        k=k,
        seed=seed,
    )


def test_plan_cells_produces_one_cell_per_grid_point_and_fold():
    assert len(_cells()) == 3 * 3
    assert {c.fold for c in _cells()} == {1, 2, 3}
    assert {c.n_requested for c in _cells()} == {0, 4, 12}


def test_plan_cells_keeps_every_bootstrap_key_in_every_train_set():
    for cell in _cells():
        assert set(_BOOT_KEYS) <= set(cell.train_keys)


def test_plan_cells_holdout_is_identical_across_n_for_a_fold():
    """hold-out은 N과 무관해야 한다 — 셀마다 평가 대상이 바뀌면 곡선을 비교할 수 없다."""
    by_fold = {}
    for cell in _cells():
        by_fold.setdefault(cell.fold, set()).add(cell.holdout_keys)

    assert all(len(v) == 1 for v in by_fold.values())


def test_plan_cells_cohort_is_fixed_per_fold_regardless_of_n():
    by_fold = {}
    for cell in _cells():
        by_fold.setdefault(cell.fold, set()).add(cell.eval_cohort)

    assert all(len(v) == 1 for v in by_fold.values())


def test_plan_cells_cohort_is_the_holdout_queries_answerable_by_the_full_train_bank():
    for cell in _cells(n_grid=(12,)):
        lab_of = dict(zip(_CUR_KEYS, _CUR_LABS, strict=True))
        train_labs = set(_BOOT_LABS) | {lab_of[k] for k in cell.train_keys if k in lab_of}
        expected = tuple(q for q in cell.holdout_keys if lab_of[q] in train_labs)
        assert cell.eval_cohort == expected


def test_plan_cells_train_and_holdout_never_intersect():
    for cell in _cells():
        assert set(cell.train_keys) & set(cell.holdout_keys) == set()


def test_plan_cells_reports_actual_curated_counts_not_the_request():
    for cell in _cells(n_grid=(0, 4, 12)):
        assert cell.n_actual_pairs <= cell.n_requested
        assert cell.n_actual_pairs == len(set(cell.train_keys) - set(_BOOT_KEYS))
        assert cell.n_actual_jobs == len({k.split("/", 1)[0] for k in cell.train_keys if "/" in k})


def test_plan_cells_zero_grid_point_trains_on_bootstrap_only():
    zero = [c for c in _cells() if c.n_requested == 0]

    assert zero and all(set(c.train_keys) == set(_BOOT_KEYS) for c in zero)
    assert all(c.n_actual_pairs == 0 and c.n_actual_jobs == 0 for c in zero)


def test_plan_cells_deduplicates_and_sorts_a_duplicated_unsorted_n_grid():
    """중복·역순 n_grid(run_curve의 [0, 25, 50, len(cur_keys)]가 우연히 겹치거나 사용자가
    --curated-n을 뒤섞어 줄 때)는 fold당 distinct N 하나씩만, 오름차순으로 셀을 만든다."""
    cells = _cells(n_grid=(50, 0, 25, 50))

    for fold in {c.fold for c in cells}:
        by_fold = [c.n_requested for c in cells if c.fold == fold]
        assert by_fold == [0, 25, 50]


def test_cell_is_frozen():
    cell = _cells()[0]

    assert isinstance(cell, Cell)
    with pytest.raises(FrozenInstanceError):
        cell.fold = 9


# --- cohort 고정 채점 ---


def _cell(cohort, holdout=None):
    return Cell(
        n_requested=0,
        fold=1,
        train_keys=(),
        holdout_keys=tuple(holdout if holdout is not None else cohort),
        n_actual_pairs=0,
        n_actual_jobs=0,
        eval_cohort=tuple(cohort),
    )


def test_score_cell_counts_a_top1_hit_against_the_cohort_denominator():
    rec = score_cell(
        emb_q=np.array([[1.0, 0.0]], np.float32),
        q_keys=["job-3/row-0"],
        q_labs=["A"],
        q_invs=["job-3"],
        emb_b=np.array([[1.0, 0.0], [0.0, 1.0]], np.float32),
        b_keys=["job-1/row-0", "job-2/row-0"],
        b_labs=["A", "B"],
        b_invs=["job-1", "job-2"],
        cell=_cell(["job-3/row-0"]),
    )

    assert rec == {"t1": 1, "t5": 1, "n_cohort": 1, "n_covered": 1}


def test_score_cell_scores_a_cohort_query_without_a_peer_as_a_miss_not_an_exclusion():
    """작은 N에서 peer가 사라진 cohort 쿼리는 분모에 남고 분자에서만 빠진다."""
    rec = score_cell(
        emb_q=np.array([[1.0, 0.0]], np.float32),
        q_keys=["job-3/row-0"],
        q_labs=["Z"],
        q_invs=["job-3"],
        emb_b=np.array([[1.0, 0.0]], np.float32),
        b_keys=["job-1/row-0"],
        b_labs=["A"],
        b_invs=["job-1"],
        cell=_cell(["job-3/row-0"]),
    )

    assert rec == {"t1": 0, "t5": 0, "n_cohort": 1, "n_covered": 0}


def test_score_cell_excludes_bank_items_from_the_query_invoice():
    """같은 전표 항목이 뱅크에 있으면 제외된다 — 남는 peer가 없으면 miss다."""
    rec = score_cell(
        emb_q=np.array([[1.0, 0.0]], np.float32),
        q_keys=["job-3/row-0"],
        q_labs=["A"],
        q_invs=["job-3"],
        emb_b=np.array([[1.0, 0.0]], np.float32),
        b_keys=["job-3/row-9"],
        b_labs=["A"],
        b_invs=["job-3"],
        cell=_cell(["job-3/row-0"]),
    )

    assert rec == {"t1": 0, "t5": 0, "n_cohort": 1, "n_covered": 0}


def test_score_cell_excludes_a_bank_item_whose_key_matches_the_query_even_if_its_inv_disagrees():
    """D4 — bank_update._axis_excluded와 같은 불변식: self_inv만으로는 뱅크 항목의 inv가
    자기 자신과 어긋날 때 못 잡는다. self_ref도 함께 넘겨야 key 일치만으로도 확실히
    제외되고, 안 그러면 이 항목이 유사도 1.0으로 자기 자신을 맞혀버린다."""
    rec = score_cell(
        emb_q=np.array([[1.0, 0.0]], np.float32),
        q_keys=["job-3/row-0"],
        q_labs=["A"],
        q_invs=["job-3"],
        emb_b=np.array([[1.0, 0.0]], np.float32),
        b_keys=["job-3/row-0"],  # 쿼리 자신의 key
        b_labs=["A"],
        b_invs=["job-9"],  # 그러나 inv는 어긋난다
        cell=_cell(["job-3/row-0"]),
    )

    assert rec == {"t1": 0, "t5": 0, "n_cohort": 1, "n_covered": 0}


def test_score_cell_counts_a_top5_only_hit_separately():
    """정답이 1위는 아니지만 dedup top-5 안에 있으면 t5만 오른다."""
    emb_b = np.array(
        [[1.0, 0.0], [0.95, 0.31], [0.9, 0.44], [0.85, 0.53], [0.8, 0.6], [0.7, 0.71]],
        np.float32,
    )
    rec = score_cell(
        emb_q=np.array([[1.0, 0.0]], np.float32),
        q_keys=["job-9/row-0"],
        q_labs=["F"],
        q_invs=["job-9"],
        emb_b=emb_b,
        b_keys=[f"job-{i}/row-0" for i in range(1, 7)],
        b_labs=["A", "B", "C", "D", "F", "G"],
        b_invs=[f"job-{i}" for i in range(1, 7)],
        cell=_cell(["job-9/row-0"]),
    )

    assert rec == {"t1": 0, "t5": 1, "n_cohort": 1, "n_covered": 1}


def test_score_cell_ignores_holdout_queries_outside_the_cohort():
    """cohort 밖 hold-out 쿼리는 채점하지 않는다(분모 고정이 목적)."""
    rec = score_cell(
        emb_q=np.array([[1.0, 0.0], [0.0, 1.0]], np.float32),
        q_keys=["job-3/row-0", "job-3/row-1"],
        q_labs=["A", "B"],
        q_invs=["job-3", "job-3"],
        emb_b=np.array([[1.0, 0.0], [0.0, 1.0]], np.float32),
        b_keys=["job-1/row-0", "job-2/row-0"],
        b_labs=["A", "B"],
        b_invs=["job-1", "job-2"],
        cell=_cell(["job-3/row-0"], holdout=["job-3/row-0", "job-3/row-1"]),
    )

    assert rec["n_cohort"] == 1 and rec["t1"] == 1


def test_score_cell_returns_all_zero_counts_for_an_empty_cohort():
    """빈 cohort는 임베딩 인덱싱 없이 네 카운트 모두 0이어야 한다."""
    rec = score_cell(
        emb_q=np.zeros((0, 2), np.float32),
        q_keys=[],
        q_labs=[],
        q_invs=[],
        emb_b=np.array([[1.0, 0.0]], np.float32),
        b_keys=["job-1/row-0"],
        b_labs=["A"],
        b_invs=["job-1"],
        cell=_cell([]),
    )

    assert rec == {"t1": 0, "t5": 0, "n_cohort": 0, "n_covered": 0}


def test_score_cell_fails_fast_when_a_cohort_key_is_not_among_the_queries():
    with pytest.raises(KeyError, match="job-9/row-0"):
        score_cell(
            emb_q=np.array([[1.0, 0.0]], np.float32),
            q_keys=["job-3/row-0"],
            q_labs=["A"],
            q_invs=["job-3"],
            emb_b=np.array([[1.0, 0.0]], np.float32),
            b_keys=["job-1/row-0"],
            b_labs=["A"],
            b_invs=["job-1"],
            cell=_cell(["job-9/row-0"]),
        )
