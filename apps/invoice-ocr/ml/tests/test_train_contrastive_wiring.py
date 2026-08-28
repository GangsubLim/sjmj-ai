"""train_contrastive 배선 가드 — 소스를 AST로만 검사한다.

이 모듈은 모듈 레벨 torch/torchvision 의존이라 CI(worker+cv)에서 import가 불가하다.
런타임 정확도가 아니라 (1) 삭제된 label_inspect 체인의 잔재 0, (2) curve 모드 인자 표면,
(3) hold-out 무오염(고정 epoch 학습에 채점·조기종료 없음), (4) 배포 워커가 import하는
심볼 보존이라는 구조 불변식만 지킨다. 실행 검증은 Task 7의 로컬 스모크가 담당한다.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "handwriting" / "train_contrastive.py"


def _tree():
    return ast.parse(SRC.read_text(encoding="utf-8"))


def _fn(name):
    return next(
        (n for n in ast.walk(_tree()) if isinstance(n, ast.FunctionDef) and n.name == name), None
    )


def _defined_names():
    tree = _tree()
    out = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return out


def _called_names(fn):
    """fn 안의 호출 이름 전체 — `foo(...)`와 `obj.foo(...)` 양쪽 형태 모두 포함."""
    out = set()
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        if isinstance(n.func, ast.Name):
            out.add(n.func.id)
        elif isinstance(n.func, ast.Attribute):
            out.add(n.func.attr)
    return out


def _cli_flags():
    """ap.add_argument(...)의 첫 위치 인자 문자열을 모은다."""
    return {
        n.args[0].value
        for n in ast.walk(_tree())
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add_argument"
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and isinstance(n.args[0].value, str)
    }


# --- 삭제된 학습 입력 체인의 잔재 0 ---


def test_the_label_inspect_chain_is_gone():
    src = SRC.read_text(encoding="utf-8")

    assert "label_inspect" not in src
    assert _defined_names() & {"prepare", "merge_resolver", "CORR", "CACHE"} == set()


def test_no_orphan_imports_remain_from_the_removed_loader():
    imported = {
        alias.name for n in ast.walk(_tree()) if isinstance(n, ast.Import) for alias in n.names
    }
    from_modules = {n.module for n in ast.walk(_tree()) if isinstance(n, ast.ImportFrom)}

    assert "hashlib" not in imported
    assert "cv2" not in imported
    assert "fewshot" not in from_modules


# --- 배포 워커 import 표면 보존 ---


def test_the_worker_import_surface_survives():
    """infer_photo.py:39가 `from train_contrastive import EVAL_TF, build_model`을 한다."""
    assert {"EVAL_TF", "build_model", "ItemEncoder", "SupConLoss", "embed"} <= _defined_names()


def test_the_functions_deferred_to_ac3_are_kept():
    assert {
        "train_split",
        "train_production",
        "split_invoices",
        "retrieval",
        "conf_gate",
    } <= _defined_names()


# --- curve 모드 인자 표면 ---


def test_curve_mode_exposes_every_documented_flag():
    assert {
        "--bootstrap-npz",
        "--pairs-jsonl",
        "--reviewed-json",
        "--crops-root",
        "--curated-n",
        "--folds",
        "--holdout-fold",
        "--baseline-ckpt",
    } <= _cli_flags()


def test_curve_is_a_positional_mode_so_existing_flag_only_invocations_still_parse():
    """`nargs="?"`가 없으면 mode가 필수 위치인자가 되어 flag-only 호출이 깨진다."""
    call = next(
        n
        for n in ast.walk(_tree())
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add_argument"
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == "mode"
    )
    nargs = next(kw.value.value for kw in call.keywords if kw.arg == "nargs")

    assert nargs == "?"


# --- hold-out 무오염 ---


def test_train_fixed_never_scores_or_early_stops_or_checkpoints():
    """학습곡선의 hold-out은 학습 종료 후 1회만 채점돼야 한다(조기종료 선택 누수 차단)."""
    fn = _fn("train_fixed")

    assert fn is not None
    called = _called_names(fn)
    assert called & {"retrieval", "score_cell", "score_pair"} == set()
    src = ast.get_source_segment(SRC.read_text(encoding="utf-8"), fn)
    assert "torch.save" not in src
    assert "patience" not in src


def test_run_curve_scores_each_cell_through_train_input():
    fn = _fn("run_curve")

    assert fn is not None
    assert {"train_fixed", "score_pair"} <= _called_names(fn)


def test_score_pair_delegates_scoring_to_train_input_score_cell():
    src = ast.get_source_segment(SRC.read_text(encoding="utf-8"), _fn("score_pair"))

    assert "score_cell" in src


def test_the_baseline_path_loads_a_checkpoint_without_training():
    fn = _fn("load_ckpt_model")

    assert fn is not None
    assert _called_names(fn) & {"train_fixed", "train_production", "train_split"} == set()


def test_the_frozen_baseline_model_loads_once_outside_the_cell_loop():
    """루프 안에서 적재하면 build_model 초기화가 전역 torch RNG를 소비해, 재학습 셀의 초기화까지
    --baseline-ckpt 지정 여부로 갈라진다(N3, E6) — 그래서 loop 밖 1회 적재가 확정 결정이다.
    """
    fn = _fn("run_curve")
    src = SRC.read_text(encoding="utf-8")
    loop = next(
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == "cell"
    )
    loop_nodes = set(ast.walk(loop))
    calls = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "load_ckpt_model"
    ]

    assert calls, "load_ckpt_model 호출이 run_curve에서 사라짐"
    assert [c for c in calls if c in loop_nodes] == []
    assert "load_ckpt_model" not in ast.get_source_segment(src, loop)


# --- 셀 간 비교 가능성 ---


def test_cell_seed_depends_only_on_fold_so_every_n_shares_random_numbers():
    """같은 fold의 모든 N이 동일 초기화·셔플에서 출발해야 곡선 차이가 N 효과만 남는다."""
    src = SRC.read_text(encoding="utf-8")
    seeds = [
        ast.get_source_segment(src, n.args[0])
        for n in ast.walk(_fn("run_curve"))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("manual_seed", "seed")
    ]

    assert len(seeds) == 2
    assert all("n_requested" not in s for s in seeds)
    assert all("cell.fold" in s for s in seeds)
    assert all("".join(s.split()) == "SEED+cell.fold*1000" for s in seeds), (
        "확정 공식(SEED + cell.fold * 1000)이 아닌 다른 N-상관 표현으로도 위 두 단언은 통과한다"
    )


def test_the_cell_loop_skips_a_cell_whose_train_bank_is_empty():
    """--bootstrap-npz 생략 + N=0이면 embed가 빈 배열에서 죽으므로 셀 자체를 건너뛴다."""
    guards = [
        n
        for n in ast.walk(_fn("run_curve"))
        if isinstance(n, ast.If)
        and isinstance(n.test, ast.UnaryOp)
        and isinstance(n.test.op, ast.Not)
        and isinstance(n.test.operand, ast.Name)
        and n.test.operand.id == "tr_idx"
    ]

    assert len(guards) == 1
    assert any(isinstance(n, ast.Continue) for n in ast.walk(guards[0]))
