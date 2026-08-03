"""tools.warp_gate_rows — 재워프·행 재현 글루(합성 데이터만, 실데이터 비의존)."""

from pathlib import Path

import pytest

pytest.importorskip("cv2", exc_type=ImportError)
np = pytest.importorskip("numpy")

from tools.warp_gate_rows import (  # noqa: E402
    STATUS_OK,
    STATUS_QUAD_MISSING,
    STATUS_REWARP_FAILED,
    STATUS_UPLOAD_MISSING,
    STATUS_UPLOAD_UNREADABLE,
    job_metrics,
    rewarp_job,
)

_PAGE_BLUE = (255, 120, 40)


def _write_png(path, bgr):
    import cv2

    cv2.imwrite(str(path), bgr)


def _photo_with_tilted_grid(angle_deg=4.0):
    """빈 종이를 촬영한 원본 사진을 흉내내는 합성 입력 — 축정렬 파랑 테두리(=quad) +
    quad와 무관한 각도로 기운 내부 격자선.

    테두리는 축정렬로 그려서 코너검출(form_quad_robust)이 직결로 찾게 하고, warp는 quad
    코너만 dst 사각형에 맞추므로 결과 테두리는 항상 축정렬로 나온다(원근변환의 정의) —
    잔여 기울기를 재현하려면 quad 자체가 아니라 quad *내부* 내용물을 quad와 무관한 각도로
    그려야 한다(rectify.py 모듈 docstring이 말하는 "4-코너 워프는 모서리만 맞춘다" 문제의
    재현). 이 잔여 기울기가 rewarp의 deskew 단계(rotate+deskew_angle)가 실제로 지우는
    대상이다.
    """
    import cv2

    from handwriting.grid_v4 import WARP_H, WARP_W

    canvas_w, canvas_h = WARP_W + 100, WARP_H + 100
    page_x0, page_y0 = 50, 50
    img = np.full((canvas_h, canvas_w, 3), 255, np.uint8)
    cv2.rectangle(img, (page_x0, page_y0), (page_x0 + WARP_W, page_y0 + WARP_H), _PAGE_BLUE, 15)

    overlay = np.full((canvas_h, canvas_w, 3), 255, np.uint8)
    y_start, pitch, n_lines, thickness = page_y0 + 620, 83, 16, 10
    for k in range(n_lines):
        y = y_start + k * pitch
        cv2.line(overlay, (page_x0 + 20, y), (page_x0 + WARP_W - 20, y), _PAGE_BLUE, thickness)
    center = (page_x0 + WARP_W / 2, page_y0 + WARP_H / 2)
    rot = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    tilted = cv2.warpAffine(
        overlay, rot, (canvas_w, canvas_h), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255)
    )
    mask = np.any(tilted != 255, axis=2)
    img[mask] = tilted[mask]
    return img


def test_rewarp_job_reports_missing_upload_without_reading_anything(tmp_path):
    def _spy(_path):
        raise AssertionError("missing upload must short-circuit before any read")

    status, warped = rewarp_job(tmp_path / "nope.jpg", loader=_spy)
    assert status == STATUS_UPLOAD_MISSING
    assert warped is None


def test_rewarp_job_reports_unreadable_upload(tmp_path):
    # 파일은 존재하고 실제로는 읽을 수 있는 유효 이미지지만, 주입한 loader가 대신
    # 읽기 실패를 낸다 — loader 인자가 실제로 쓰이는지(무시하고 기본 로더로 대체하지
    # 않는지)를 증명한다. 그래야 62잡 전수 리포트가 중간에 죽지 않아야 한다는 계약을
    # loader 구현체 무관하게 검증한 것이 된다.
    valid = tmp_path / "broken.jpg"
    _write_png(valid, np.full((10, 10, 3), 255, np.uint8))

    def _boom(_path):
        raise OSError("synthetic decode failure")

    status, warped = rewarp_job(valid, loader=_boom)
    assert status == STATUS_UPLOAD_UNREADABLE
    assert warped is None


def test_rewarp_job_reports_unreadable_upload_on_decompression_bomb(tmp_path, monkeypatch):
    # PIL.Image.DecompressionBombError는 Exception 직계라 OSError로 안 잡힌다 — 좁힌
    # except 튜플이 이 타입을 놓치면 초대형 업로드 1장이 전수 리포트를 중간에 죽인다.
    # 기본 loader(load_bgr_path → PIL.Image.open) 경로로 실제 예외 타입을 재현한다.
    from PIL import Image

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    huge = tmp_path / "huge.png"
    _write_png(huge, np.full((50, 50, 3), 255, np.uint8))

    status, warped = rewarp_job(huge)
    assert status == STATUS_UPLOAD_UNREADABLE
    assert warped is None


def test_rewarp_job_reports_unreadable_upload_when_the_loader_returns_none(tmp_path):
    # cv2.imread 관용구 로더는 디코딩 실패를 예외가 아니라 None으로 알린다(같은 레포
    # tools/blank_crop_calib.py가 이미 인정한 실패 모드) — None을 그대로 rewarp에 흘리면
    # "예외를 던지지 않는다"는 rewarp_job의 계약이 loader 구현체에 따라 샌다.
    stub = tmp_path / "photo.jpg"
    stub.write_bytes(b"stand-in bytes, the injected loader ignores this")

    status, warped = rewarp_job(stub, loader=lambda _path: None)
    assert status == STATUS_UPLOAD_UNREADABLE
    assert warped is None


def test_rewarp_job_degrades_when_the_warp_pipeline_itself_raises(tmp_path, monkeypatch):
    # rewarp는 전부 cv2 글루(form_quad_robust·warp·deskew_angle·rotate)다 — 병리적 원본
    # 1장의 cv2.error가 try 밖에 있으면 62잡 전수 순회가 통째로 중단되고 앞서 치른 원본
    # 사진 fetch 비용까지 날아간다. 이 계약이 막으려던 시나리오 그 자체다.
    import cv2

    import tools.warp_gate_rows as rows

    stub = tmp_path / "photo.jpg"
    stub.write_bytes(b"stand-in bytes, the injected loader ignores this")

    def _boom(_bgr):
        raise cv2.error("synthetic cv2 failure")

    monkeypatch.setattr(rows, "rewarp", _boom)

    status, warped = rows.rewarp_job(stub, loader=lambda _path: np.zeros((10, 10, 3), np.uint8))
    assert status == STATUS_REWARP_FAILED
    assert warped is None


def test_rewarp_job_reports_quad_missing_when_the_sheet_has_no_form(tmp_path):
    # monkeypatch로 _form_quad를 대신하지 않는다 — 실측: 흰 배경만 있는 이미지에서
    # form_quad_robust는 실제로 None을 반환한다. 이 경로가 운영과 동일한 quad 검출을
    # 그대로 통과해야 quad 미검출을 흉내내는 변이(_form_quad 조작)도 함께 잡힌다.
    blank = tmp_path / "blank.png"
    _write_png(blank, np.full((400, 300, 3), 255, np.uint8))
    status, warped = rewarp_job(blank)
    assert status == STATUS_QUAD_MISSING
    assert warped is None


def test_rewarp_job_returns_the_template_sized_rewarp_via_injected_loader(tmp_path):
    from handwriting.grid_v4 import WARP_H, WARP_W

    photo = _photo_with_tilted_grid()
    stub = tmp_path / "photo.jpg"
    stub.write_bytes(b"stand-in bytes, the injected loader ignores this")

    status, warped = rewarp_job(stub, loader=lambda _path: photo)

    assert status == STATUS_OK
    assert warped.shape == (WARP_H, WARP_W, 3)


def test_rewarp_job_deskews_the_residual_tilt_inside_the_detected_quad(tmp_path):
    from handwriting.rectify import deskew_angle

    photo = _photo_with_tilted_grid()
    stub = tmp_path / "photo.jpg"
    stub.write_bytes(b"stand-in bytes, the injected loader ignores this")

    status, warped = rewarp_job(stub, loader=lambda _path: photo)

    assert status == STATUS_OK
    # 테두리(quad)는 warp만으로 항상 축정렬이 되므로, deskew 단계가 생략돼도 shape는
    # 안 바뀐다 — 그 대신 quad 내부에 준 잔여 기울기(약 4도)가 그대로 남는다. deskew가
    # 실행됐다면 이 잔여 기울기가 거의 0으로 지워져 있어야 한다.
    assert abs(deskew_angle(warped)) < 1.0


def test_job_metrics_carries_both_mask_axes(make_warped):
    m = job_metrics(make_warped())
    assert set(m) == {"std", "enh"}
    assert m["std"]["hline_count"] == 16
    assert m["enh"]["hline_count"] > 0


def test_job_metrics_enh_axis_uses_the_enhanced_mask_not_std(make_warped):
    # make_warped()의 강한 파랑(255,120,40)은 표준·enh 마스크 모두를 통과시켜 두 축이 우연히
    # 같은 값이 나온다 — enhanced=True 배선이 빠져도 위 테스트의 "enh > 0"는 못 잡는다.
    # b−r=10의 옅은 파랑은 표준 마스크에서는 0선, enh 마스크에서만 16선이 잡히는 입력
    # (plan "좌표 재검증 결과" §2 실측치)이라 std/enh 배선 자체를 강제한다. 격자 좌표
    # (y_start/pitch/thickness)는 make_warped 기본값과 동일하게 두고 color만 옅은 파랑으로
    # 바꿔 test_warp_gate.py의 _faint_grid()와 동일 입력을 재현한다.
    img = make_warped(color=(250, 120, 240))

    m = job_metrics(img)
    assert m["std"]["hline_count"] == 0
    assert m["enh"]["hline_count"] == 16


# --- 행·크롭 재현(Task 7) ---


def test_replicate_rows_reproduces_bands_and_crops_without_any_model(make_warped):
    # 워프 경로와 크롭 경계 산출은 전부 cv2/numpy 전용이다 — Fake read_fn만 있으면
    # 밴드 수·new 수·크롭 좌표가 모델 없이 재현된다(spec §2).
    # 격자선뿐인 make_warped() 기본 입력은 모든 밴드가 empty로 분류돼 n_new가 0이 되고,
    # 그러면 아래 길이 일치는 0 == 0 == 0, all(...)은 빈 리스트 위 vacuous True다 —
    # 손글씨 행을 하나 넣고 n_new > 0을 먼저 못박아 공허 통과를 막는다.
    from tools.warp_gate_rows import replicate_rows

    snap = replicate_rows(_with_one_handwritten_row(make_warped()))
    assert snap["n_bands"] > 0
    assert snap["n_new"] > 0
    assert len(snap["boxes"]) == snap["n_new"] == len(snap["crop_sha"])
    assert all(len(b) == 2 and b[1] > b[0] for b in snap["boxes"])


def test_replicate_rows_is_deterministic(make_warped):
    # 잡 59~63은 업로드 md5가 같은 한 장의 사진이다 — 다섯 산출이 서로 완전히 일치해야
    # 하며(spec §4.3), 그 전제는 이 함수의 결정론이다. 격자선뿐인 입력으로는 비교되는 것이
    # 정수 두 개뿐이라 정작 그 근거인 크롭 해시 경로가 한 번도 실행되지 않는다.
    from tools.warp_gate_rows import replicate_rows

    img = _with_one_handwritten_row(make_warped())

    snap = replicate_rows(img)
    assert snap["crop_sha"], "해시 경로가 실행되지 않으면 결정론 비교가 공허하다"
    assert snap == replicate_rows(img)


def test_crop_digest_distinguishes_pixel_content():
    from tools.warp_gate_rows import crop_digest

    a = np.zeros((10, 10, 3), np.uint8)
    b = a.copy()
    b[0, 0] = 1
    assert crop_digest(a) != crop_digest(b)


def test_crop_digest_separates_arrays_that_share_bytes_but_differ_in_shape():
    # 픽셀 바이트열만 해시하면 (6,2,3)과 (4,3,3)이 같은 해시가 된다 — 크롭 경계가
    # 밀려 shape가 바뀌었는데 '무변경'으로 보고되는 축 ②-a의 침묵 구멍이다.
    from tools.warp_gate_rows import crop_digest

    flat = np.zeros(36, np.uint8)
    assert crop_digest(flat.reshape(6, 2, 3)) != crop_digest(flat.reshape(4, 3, 3))


def test_crop_digest_is_a_fixed_length_hex_prefix():
    # 스냅샷 JSON 크기와 충돌 확률의 절충값이다 — 길이가 조용히 바뀌면 기존 베이스라인과
    # 전 잡이 changed로 뒤집힌다.
    from tools.warp_gate_rows import CROP_DIGEST_CHARS, crop_digest

    sha = crop_digest(np.zeros((4, 4, 3), np.uint8))
    assert len(sha) == CROP_DIGEST_CHARS == 16
    assert all(c in "0123456789abcdef" for c in sha)


def test_item_crop_slices_the_item_column_with_the_production_padding():
    # 해시 대상과 육안검수 PNG가 이 함수 하나를 공유한다 — 기하가 갈라지면 '해시한 것'과
    # '눈으로 본 것'이 달라져 축 ②-b의 육안 근거가 무의미해진다.
    from handwriting.rows import ITEM_X
    from tools.warp_gate_rows import ITEM_CROP_PAD, item_crop

    warped = np.arange(30 * 2100 * 3, dtype=np.uint8).reshape(30, 2100, 3)
    x1, x2 = ITEM_X
    assert ITEM_CROP_PAD == 4
    assert np.array_equal(item_crop(warped, [5, 15]), warped[5:15, x1 - 4 : x2 + 4])


def test_crop_ink_counts_only_pixels_darker_than_the_threshold():
    # 축 ②-b의 유일한 정량 신호다 — 임계가 밀리면 백지 크롭이 '잉크 있음'으로 보고된다.
    # 경계값은 상수를 읽지 않고 리터럴로 못박는다: CROP_INK_MAX_LEVEL을 참조하면 임계를
    # 200으로 올리는 변이를 테스트가 따라가 버려 아무것도 고정하지 못한다.
    from tools.warp_gate_rows import crop_ink

    blank = np.full((10, 10, 3), 255, np.uint8)
    half_dark = blank.copy()
    half_dark[:, :5] = 0

    assert crop_ink(blank) == 0.0
    assert crop_ink(half_dark) == 0.5
    assert crop_ink(np.full((4, 4, 3), 119, np.uint8)) == 1.0
    assert crop_ink(np.full((4, 4, 3), 120, np.uint8)) == 0.0


def _with_one_handwritten_row(img):
    """make_warped() 격자 위에 손글씨를 흉내낸 획을 더해 new행 하나를 강제로 만든다.

    make_warped 기본 입력은 격자선뿐이라 모든 밴드가 empty로 분류돼 boxes가 비고 crop_sha·
    crop_ink 자체가 계산되지 않는다 — 크롭 축을 검증하려면 실제 new행이 하나는 있어야 한다.
    """
    import cv2

    from handwriting.grid_v4 import AMOUNT_X
    from handwriting.rows import ITEM_X

    x1, _x2 = ITEM_X
    ax1, _ax2 = AMOUNT_X
    for i in range(10):
        yy = 890 + i * 4
        cv2.line(img, (x1 + 10, yy), (x1 + 80, yy + 3), (0, 0, 0), 2)
        cv2.line(img, (ax1 + 10, yy), (ax1 + 120, yy + 3), (0, 0, 0), 2)
    return img


def test_replicate_rows_crop_sha_matches_the_item_column_padded_slice(make_warped):
    # crop_sha가 실제로 ITEM_X ±4px 패딩 슬라이스의 해시인지 고정한다 — n_bands/n_new/boxes
    # shape만 보는 첫 재현 테스트는 크롭의 x-bounds가 좁혀져도(패딩 소실) 못 잡는다.
    from tools.warp_gate_rows import crop_digest, item_crop, replicate_rows

    img = _with_one_handwritten_row(make_warped())

    snap = replicate_rows(img)
    assert snap["n_new"] == 1
    box = snap["boxes"][0]
    assert snap["crop_sha"][0] == crop_digest(item_crop(img, box))


def test_replicate_rows_reports_the_ink_ratio_of_the_hashed_crop(make_warped):
    # crop_ink는 축 ②-b('구제된 잡의 크롭이 쓰레기가 아닌가')의 유일한 정량 신호다 —
    # 전량 0.0으로 상수화돼도 boxes/crop_sha만 보는 테스트는 초록으로 통과한다.
    from tools.warp_gate_rows import crop_ink, item_crop, replicate_rows

    img = _with_one_handwritten_row(make_warped())

    snap = replicate_rows(img)
    assert snap["crop_ink"] == [crop_ink(item_crop(img, snap["boxes"][0]))]
    assert snap["crop_ink"][0] > 0.0


def _detected_band_count(img):
    """운영 detect_grid_rows가 실제로 내는 밴드 수(하네스와 독립 경로로 계산)."""
    from handwriting.canon import global_pitch
    from handwriting.grid_v4 import DATA_Y, hline_ys
    from handwriting.rows import detect_grid_rows

    y0, y1 = DATA_Y
    ys = [y for y in hline_ys(img) if y0 - 40 <= y <= y1 + 40]
    return len(detect_grid_rows(img, global_pitch({"x": ys})))


def test_replicate_rows_counts_the_bands_that_were_actually_detected(make_warped):
    # n_bands가 상수로 굳어도 "len(boxes) == n_new"만 보는 재현 테스트는 못 잡는다 —
    # 밴드 수는 축 ②-b에서 '행 검출 자체가 무너졌는지'를 보는 첫 신호다. 밴드 수가 서로
    # 다른 두 입력(실측: 피치 83px→12밴드, 60px→19밴드)을 함께 보면 어떤 상수화도 걸린다.
    from tools.warp_gate_rows import replicate_rows

    sparse, dense = make_warped(), make_warped(n_lines=20, pitch=60)
    assert _detected_band_count(sparse) != _detected_band_count(dense), "상수화 배제의 전제"

    assert replicate_rows(sparse)["n_bands"] == _detected_band_count(sparse)
    assert replicate_rows(dense)["n_bands"] == _detected_band_count(dense)


# --- 하네스↔운영 배선 가드 ---
#
# infer_photo는 모듈 최상단 torch import 때문에 여기서 import할 수 없어 소스를 AST로만 대조한다.
# 아래 세 가드는 각각 다른 드리프트 축을 본다: 호출 이름 집합(단계 누락) · build_proposal
# keyword(임계 스왑/상수화) · hline_ys 창(±40 소실). 이름 집합 하나만으로는 인자 드리프트를
# 전혀 못 잡는다.

_ML_ROOT = Path(__file__).resolve().parents[1]
_PROD_SRC = (_ML_ROOT / "handwriting" / "infer_photo.py", "extract_rows_for_job")
_HARNESS_SRC = (_ML_ROOT / "tools" / "warp_gate_rows.py", "replicate_rows")


def _function_def(src):
    import ast

    path, fn_name = src
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn_name)


def _call_keywords(src, callee):
    """대상 함수가 `callee(...)`에 넘기는 keyword 인자를 {이름: 값 소스}로 뽑는다."""
    import ast

    fn = _function_def(src)
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == callee:
            return {k.arg: ast.unparse(k.value) for k in n.keywords}
    raise AssertionError(f"{callee} 호출을 찾지 못했다: {src}")


def _hline_filter_shape(src):
    """`hline_ys(...)`를 도는 리스트 컴프리헨션의 구조 — 식별자 이름은 지운다.

    운영은 `w`/`Y0`/`Y1`, 하네스는 `warped`/`y0`/`y1`을 쓰므로 이름을 그대로 두면 절대 같아지지
    않는다. Name을 전부 `_`로 접으면 남는 것은 구조와 상수(±40)뿐이다.
    """
    import ast
    import copy

    class _AnonymizeNames(ast.NodeTransformer):
        def visit_Name(self, node):
            return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    fn = _function_def(src)
    for n in ast.walk(fn):
        if isinstance(n, ast.ListComp) and any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "hline_ys"
            for c in ast.walk(n)
        ):
            return ast.dump(_AnonymizeNames().visit(copy.deepcopy(n)))
    raise AssertionError(f"hline_ys 컴프리헨션을 찾지 못했다: {src}")


def test_replicate_rows_calls_the_same_pipeline_as_extract_rows_for_job():
    # 모델 의존(embed_crops)을 뺀 나머지 호출 집합이 같아야 한다. 금액 전사(read_amount)는
    # 운영에서 중첩 클로저 read_fn 안에 있어 callees가 이미 제외하므로 여기 적지 않는다 —
    # 훗날 클로저 밖으로 끌어올려지면 이 가드가 빨개지고, 그때 다시 model_only에 넣으면 된다.
    import ast

    model_only = {"embed_crops"}

    def callees(src):
        """대상 함수가 **직접** 부르는 이름 호출 집합. 중첩 FunctionDef는 제외한다.

        infer_photo.extract_rows_for_job에는 금액 전사 클로저 read_fn이 있고 그 안의
        next(counter)까지 ast.walk가 수집해 버린다(재현: 차집합 {next}). 비교 대상은
        '파이프라인 호출 순서'이지 클로저 내부 구현이 아니다.
        """
        fn = _function_def(src)
        nested = {
            id(d)
            for n in ast.walk(fn)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n is not fn
            for d in ast.walk(n)
        }
        return {
            n.func.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and id(n) not in nested
        }

    prod = callees(_PROD_SRC) - model_only
    harness = callees(_HARNESS_SRC)

    assert prod <= harness, f"하네스가 운영 경로와 갈라졌다: 빠진 호출 {prod - harness}"


def test_replicate_rows_passes_the_same_grouping_thresholds_as_extract_rows_for_job():
    # 호출 이름 가드는 build_proposal을 부르기만 하면 통과한다 — item_min/amt_min 스왑도
    # pad=PAD→pad=0도 잡지 못한다. 이 셋이 어긋나면 재현된 크롭 경계가 운영과 달라져
    # 축 ②-a·②-b의 증거가 통째로 무의미해진다.
    prod = _call_keywords(_PROD_SRC, "build_proposal")

    assert set(prod) == {"item_min", "amt_min", "pad"}, "가드가 볼 keyword가 사라졌다"
    assert _call_keywords(_HARNESS_SRC, "build_proposal") == prod


def test_replicate_rows_filters_hline_ys_with_the_same_window_as_extract_rows_for_job():
    # DATA_Y ±40 창이 좁아지거나(±0) 필터가 통째로 빠지면 pitch·밴드가 운영과 달라진다 —
    # 호출 이름 집합에는 `hline_ys` 하나로만 보여 어느 가드에도 걸리지 않던 축이다.
    shape = _hline_filter_shape(_PROD_SRC)

    assert "40" in shape, "가드가 볼 창 상수가 사라졌다"
    assert _hline_filter_shape(_HARNESS_SRC) == shape


def test_item_crop_pad_matches_the_padding_baked_into_extract_rows_for_job():
    # 운영 pad는 infer_photo의 인라인 슬라이스에 리터럴로 남아 있고, 하네스는 ITEM_CROP_PAD를
    # 따로 들고 있다 — 두 값을 대조하는 곳이 주석뿐이라 운영이 4→6으로 바뀌면 스냅샷이
    # 운영과 다른 픽셀을 해시하는데도 위 AST 가드 3종은 전부 초록으로 남는다.
    # ast.unparse가 공백·포맷을 정규화하므로 운영 쪽 포매팅 변화에는 견딘다.
    import ast

    from tools.warp_gate_rows import ITEM_CROP_PAD

    prod = ast.unparse(_function_def(_PROD_SRC))

    assert f"x1 - {ITEM_CROP_PAD}:x2 + {ITEM_CROP_PAD}" in prod
