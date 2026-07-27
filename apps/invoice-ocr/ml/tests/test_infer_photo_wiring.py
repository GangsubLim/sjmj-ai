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
    # read_fn 주입(=read_amount 클로저)이 빠지거나 스텁화되면 여기서 잡는다.
    assert all(len(node.value.args) == 2 for node in assigns)
    assert "read_amount" in names


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
