"""금액칸 크롭 좌측 경계의 전표별 실측 (#50).

템플릿 `AMOUNT_X[0]=612`는 종이가 캔버스를 꽉 채운 정상 전표에서도 인쇄 세로선보다 18~31px
왼쪽(단가칸 안)에 있어, 단가칸 끝 획(`×2`의 2 등)이 금액 크롭에 섞인다(잡 54 row-0 실측).
합성 워프 이미지로 세로선 검출·폴백·잡음 내성만 고정한다 — 실전표 효험은 오프라인 재현 담당.
"""

import pytest

pytest.importorskip("cv2", exc_type=ImportError)
np = pytest.importorskip("numpy")

from handwriting.grid_v4 import (  # noqa: E402
    AMOUNT_X,
    DATA_Y,
    WARP_H,
    WARP_W,
    amount_crop_left,
)

_BLUE = (200, 120, 40)  # BGR — blue_mask((b−r)>10)가 잡는 인쇄선 색


def _blank():
    return np.full((WARP_H, WARP_W, 3), 255, np.uint8)


def _vline(img, x, y0=DATA_Y[0], y1=DATA_Y[1], thickness=3):
    import cv2

    cv2.line(img, (x, y0), (x, y1), _BLUE, thickness)


def test_detected_column_line_moves_left_edge_right_of_the_line():
    img = _blank()
    _vline(img, 634)
    left = amount_crop_left(img)
    # 선 오른쪽 가장자리(634+1) 밖이되 손글씨 첫 획을 자르지 않게 몇 px만
    assert 636 <= left <= 640


def test_no_line_falls_back_to_template():
    assert amount_crop_left(_blank()) == AMOUNT_X[0]


def test_narrow_paper_line_left_of_template_is_accepted():
    # 잡 41: 종이 88% 점유 → 금액열 세로선 584, `117`의 첫 1이 612 왼쪽에서 잘림
    img = _blank()
    _vline(img, 584)
    assert 586 <= amount_crop_left(img) <= 590


def test_short_handwriting_stroke_is_not_a_column_line():
    # 세로로 긴 손글씨 획(1·ㅣ)은 한 행 높이뿐 — 데이터 구간 대부분을 덮는 인쇄선만 인정
    img = _blank()
    _vline(img, 630, y0=900, y1=980, thickness=6)
    assert amount_crop_left(img) == AMOUNT_X[0]


def test_line_outside_search_window_is_ignored():
    # 품목/수량 열 세로선(≈385·507)이나 우측 테두리(≈893)는 후보가 아니다
    img = _blank()
    _vline(img, 507)
    _vline(img, 893)
    assert amount_crop_left(img) == AMOUNT_X[0]


def test_nearest_line_to_template_wins_when_several_are_in_window():
    img = _blank()
    _vline(img, 575)
    _vline(img, 632)
    assert 634 <= amount_crop_left(img) <= 638
