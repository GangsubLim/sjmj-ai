"""공용 픽스처. 실데이터에 의존하지 않는 합성 데이터만 둔다."""

import math

import pytest

from handwriting.warp_gate import MIN_BLUE_RATIO  # stdlib만 쓰는 모듈이라 코어 venv에서도 안전

# 정상 합성의 파랑 비율을 임계의 몇 배로 둘지. 3배면 임계가 흔들려도 정상 케이스가 여유를 갖는다.
HEALTHY_RATIO_FACTOR = 3


@pytest.fixture
def make_warped():
    """합성 워프 BGR(900×2100) 생성기 — cv2/numpy가 있는 테스트에서만 요청한다.

    흰 바탕에 파랑(BGR 255,120,40) 수평 격자선을 그린다. grid_v4.hline_ys는 폭
    WARP_W//3(=300) 이상의 수평 런만 선으로 인정하므로 x_end로 '반쪽만 격자'(잡 39 유형)를,
    n_lines/pitch로 '격자 희박·피치 발산'(잡 34 유형)을 만든다.

    ⚠️ 픽스처 본문은 importorskip으로 시작한다. numpy/grid_v4 import는 테스트 본문의
       importorskip보다 **먼저** 실행되므로, 가드가 없으면 cv2 부재 환경에서 skip이 아니라
       fixture ERROR가 나고 '코어 paddle-free 회귀' 게이트가 빨갛게 죽는다.

    thickness=None(기본)이면 MIN_BLUE_RATIO에서 유도해 정상 합성의 파랑 비율이 임계의
    HEALTHY_RATIO_FACTOR배 근처가 되게 한다 — 고정 두께였다면 Task 7에서 MIN_BLUE_RATIO가
    합성값(0.036) 위로 캘리브될 때 테스트가 무조건 깨진다. 다만 두께는 최소 3(ceil 바닥값)이라
    현재 상수(MIN_BLUE_RATIO=0.004)에서는 바닥값 3이 적용돼 실효 ~9배, MIN_BLUE_RATIO>0.006부터
    유도식이 지배한다.
    n_lines는 유도하지 않는다: y_start=620·pitch=83에서 DATA_Y 창에 들어가는 선은 최대 17개라
    MIN_HLINES에서 유도하면 임계가 14 이상일 때 그린 선과 검출 선 개수가 어긋난다(실측 확인).
    """
    pytest.importorskip("cv2", exc_type=ImportError)
    np = pytest.importorskip("numpy")

    from handwriting.grid_v4 import DATA_Y, WARP_H, WARP_W

    def _make(*, n_lines=16, pitch=83, y_start=620, x_end=WARP_W, thickness=None):
        if thickness is None:
            span = DATA_Y[1] - DATA_Y[0]
            thickness = max(
                3, math.ceil(HEALTHY_RATIO_FACTOR * MIN_BLUE_RATIO * span / max(n_lines, 1))
            )
        img = np.full((WARP_H, WARP_W, 3), 255, np.uint8)
        for k in range(n_lines):
            y = y_start + k * pitch
            img[y : y + thickness, 0:x_end] = (255, 120, 40)
        return img

    return _make


@pytest.fixture
def tiny_invoices_sql() -> str:
    """invoices/invoice_items 최소 INSERT 샘플 (백업 형식 모사)."""
    return (
        "INSERT INTO `invoices` (`id`, `document_title`, `issue_date`, `recipient`, "
        "`recipient2`, `vehicle_no`, `memo`, `show_stamp`, `issuer_id`, `total_supply`, "
        "`total_vat`, `grand_total`, `created_at`, `updated_at`) VALUES\n"
        "(11, '거래명세서', '2026-05-12', '옥천운수', '이희원', '5608', '', 1, NULL, "
        "300000, 30000, 330000, '2026-05-12 05:57:39', '2026-05-12 05:57:39'),\n"
        "(12, '거래명세서', '2026-05-13', '성우항공', NULL, '3102', 'O''Brien 메모', 1, NULL, "
        "120000, 12000, 132000, '2026-05-13 08:48:53', '2026-05-13 08:48:53');\n"
        "INSERT INTO `invoice_items` (`id`, `invoice_id`, `item_order`, `name`, `quantity`, "
        "`unit`, `unit_price`, `supply`, `vat`, `total`) VALUES\n"
        "(42, 11, 1, '단지', 1, 'EA', 300000, 300000, 30000, 330000),\n"
        "(43, 12, 1, '세차', 1, 'EA', 30000, 30000, 3000, 33000),\n"
        "(44, 12, 2, '중고타이어', 1, NULL, 90000, 90000, 9000, 99000);\n"
    )
