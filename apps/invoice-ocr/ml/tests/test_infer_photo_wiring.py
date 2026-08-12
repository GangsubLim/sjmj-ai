"""extract_rows_for_job 배선 가드 — 소스를 AST로만 검사한다.

infer_photo는 모듈 레벨 cv2/torch 의존이라 paddle-free 환경에서 import가 불가하다.
런타임 정확도가 아니라 "금액 소비가 block_amounts에 위임됐는가"라는 구조 불변식만 지킨다
(런타임은 macmini 라이브 확인 담당 — plan '수동 검증 체크리스트').
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "handwriting" / "infer_photo.py"


def _extract_rows_fn():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "extract_rows_for_job"
    )
    return tree, fn


def test_amount_consumption_is_delegated_to_block_amounts():
    tree, fn = _extract_rows_fn()
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "group"
        for alias in node.names
    }
    assigns = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Tuple)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "block_amounts"
    ]
    unpacked = {tuple(el.id for el in node.targets[0].elts) for node in assigns}
    names = {node.id for node in ast.walk(fn) if isinstance(node, ast.Name)}

    assert "block_amounts" in imported
    assert unpacked == {("news", "amounts")}
    # read_fn 주입(=read_amount 클로저)이 빠지거나 스텁화되면 여기서 잡는다. 인자 개수만 세면
    # lambda 스텁(read_fn은 死코드로 잔존)과 인자 뒤바뀜을 놓치므로 2번째 인자 이름까지 못 박는다.
    assert all(len(node.value.args) == 2 for node in assigns)
    assert all(
        isinstance(node.value.args[1], ast.Name) and node.value.args[1].id == "read_fn"
        for node in assigns
    )
    assert "read_amount" in names


def test_block_amounts_results_are_not_rebound():
    # 위임해두고 뒤에서 amounts를 다시 계산해 덮어쓰면 Issue #19가 그대로 재발한다.
    # news/amounts는 block_amounts unpack에서 단 한 번만 바인딩돼야 한다.
    _, fn = _extract_rows_fn()
    stores = [
        node.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    ]

    assert stores.count("news") == 1
    assert stores.count("amounts") == 1


def test_new_row_selection_predicate_is_not_duplicated():
    # 선별 술어 리터럴("new")이 남아 있으면 block_amounts와 선별 기준이 이중화된 것.
    _, fn = _extract_rows_fn()
    literals = {node.value for node in ast.walk(fn) if isinstance(node, ast.Constant)}

    assert "new" not in literals, "new행 선별은 block_amounts가 단독 소유 — 호출부 술어 이중화 금지"


def test_item_crops_iterate_block_amounts_news():
    # 변경 전에도 GREEN인 고정(pinning) 가드 — 리터럴 검사만으로는 ROW_NEW 상수로 선별을
    # 다시 쓰는 이중화(false-GREEN)를 못 막으므로, crop 순회 대상이 news인지 못 박는다.
    _, fn = _extract_rows_fn()
    iters = {
        node.value.generators[0].iter.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "crops"
        and isinstance(node.value, ast.ListComp)
        and isinstance(node.value.generators[0].iter, ast.Name)
    }

    assert iters == {"news"}


def test_amount_read_is_imported_through_the_package_path():
    """flat `import amount_read`는 예외 클래스를 이중화해 worker의 except를 빗나가게 한다.

    infer_photo는 sys.path에 자기 디렉터리를 넣으므로 flat import가 `amount_read`라는 별개
    모듈 객체를 만든다 — 그 안의 DegenerateOutputError는 worker/poll.py가 잡는
    handwriting.amount_read.DegenerateOutputError와 다른 클래스다. 단위테스트는 양쪽 다
    패키지 경로를 써서 통과하므로, 이 정적 가드만이 운영 전용 실패를 막는다.
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in {"read_amount_with_retry", "attempt_png_name"}
    }

    assert not [
        n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "amount_read"
    ], "flat import는 예외 클래스를 이중화한다"
    assert modules == {"handwriting.amount_read"}


def _fn(name):
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    return tree, next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name
    )


def test_process_one_delegates_quad_supply_to_form_quad_best():
    """데모 CLI도 production과 같은 quad 경로를 타야 QA 도구로 유효하다."""
    _, fn = _fn("process_one")
    args = [a.arg for a in fn.args.args]
    calls = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "form_quad_best"
    ]
    names = {node.id for node in ast.walk(fn) if isinstance(node, ast.Name)}

    assert "aligner" in args
    assert len(calls) == 1
    # 2번째 인자가 aligner여야 한다 — 인자 개수만 세면 None 하드코딩을 놓친다.
    assert isinstance(calls[0].args[1], ast.Name) and calls[0].args[1].id == "aligner"
    assert "form_quad_robust" not in names, "quad 공급 이중화 금지 — 조합 함수 하나만 부른다"


def test_corner_dl_is_imported_through_the_package_path():
    """flat `import corner_dl`은 모듈 객체를 이중화한다(amount_read와 같은 함정).

    infer_photo는 sys.path에 자기 디렉터리를 넣으므로 flat import가 별개 모듈을 만들고,
    그러면 monkeypatch·상수 갱신이 한쪽에만 걸린다.
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in {"form_quad_best", "load_or_none"}
    }

    assert modules == {"handwriting.corner_dl"}
    assert not [
        n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "corner_dl"
    ], "flat import는 모듈을 이중화한다"


def test_main_loads_the_aligner_once_from_the_env_and_passes_it_down():
    """모델은 CLI 실행당 1회 적재하고(사진마다 재적재 금지), 경로는 env로만 받는다."""
    _, fn = _fn("main")
    loads = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "load_or_none"
    ]
    env_keys = {
        arg.value
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        for arg in node.args
        if isinstance(arg, ast.Constant)
    }
    handoff = [
        kw.value.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "process_one"
        for kw in node.keywords
        if kw.arg == "aligner" and isinstance(kw.value, ast.Name)
    ]
    assigned = {
        t.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "load_or_none"
        for t in node.targets
        if isinstance(t, ast.Name)
    }

    assert len(loads) == 1
    assert "SJMJ_ML_MODELS_DIR" in env_keys
    assert handoff == ["aligner"]
    assert assigned == {"aligner"}  # 적재 결과가 실제로 넘겨지는 이름에 묶이는지까지 본다
