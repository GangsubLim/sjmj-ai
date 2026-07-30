"""빈 크롭 판정 순수 술어 단위테스트(cv2/numpy 무의존 — 코어 venv에서도 돈다)."""

import json
import math
from pathlib import Path

import pytest

from handwriting.blank_crop import (
    BLANK_CROP,
    STATUS_EXCLUDED,
    STATUS_INCLUDED,
    is_blank,
    is_machine_writable,
    require_blank_ink_max,
)

# tests/test_blank_crop.py → ml → invoice-ocr → apps → repo root(선례: test_version_sync.py).
_REPO_ROOT = Path(__file__).resolve().parents[4]

# --- wire 상수 pin: DB 컬럼 값·백엔드 스키마·프론트 타입과 문자 그대로 같아야 한다 ---


def test_wire_constants_match_db_migration_and_schemas():
    """이 세 상수는 이 모듈 내부 값이 아니라 DB·backend·frontend가 공유하는 wire 값이다.

    문자열이 바뀌어도 이 파일 안 테스트는 상수만 참조하므로 전부 초록으로 남는다 — 그
    사이 `is_machine_writable`은 운영에 이미 쌓인 기존 행(예: status='excluded',
    exclusion_reason='blank_crop')을 전부 거부하게 되어, 예외 없이 기능만 조용히
    정지한다. 값이 아래 세 곳과 문자 단위로 같은지 여기서 고정한다:
      - .claude/ai-context/api-spec.json
        (CurationPair.exclusion_reason enum — 값 집합의 계약 정본. migration_009의 DDL은
         VARCHAR(32)라 값 집합을 강제하지 않으므로 앵커가 되지 못한다)
      - apps/invoice-ocr/backend/app/schemas/curation.py
        (status 화이트리스트 'included'/'excluded')
      - apps/invoice-ocr/frontend/src/types/curation.ts
        (CurationPairBase.status 유니언 타입)
    """
    assert BLANK_CROP == "blank_crop"
    assert STATUS_INCLUDED == "included"
    assert STATUS_EXCLUDED == "excluded"


def test_blank_crop_wire_value_appears_in_api_spec_enum():
    """BLANK_CROP 상수가 API 계약 정본(api-spec.json)의 enum 리터럴과 일치해야 한다.

    앵커를 migration_009로 두면 안 된다 — 그 파일에서 'blank_crop'이 나오는 자리는
    **설명 주석 한 줄뿐**이고 DDL은 `VARCHAR(32) NULL DEFAULT NULL`이라 값 집합을 강제하지
    않는다. 즉 주석 문구만 다듬어도 ml CI가 빨개지고, 정작 값 집합이 갈라지는 진짜 드리프트는
    못 잡는다. api-spec.json의 `exclusion_reason.enum`은 이 값을 실제로 싣는 계약이다
    (프론트 타입 쪽 앵커는 test_status_wire_values_appear_in_frontend_curation_types가 맡는다).
    """
    spec = json.loads((_REPO_ROOT / ".claude" / "ai-context" / "api-spec.json").read_text("utf-8"))
    enum = spec["components"]["schemas"]["CurationPair"]["properties"]["exclusion_reason"]["enum"]
    assert BLANK_CROP in enum


def test_status_wire_values_appear_in_backend_curation_schema():
    """STATUS_INCLUDED/EXCLUDED가 backend `curation.py`의 Literal 화이트리스트와 같은지 고정한다."""
    schema = _REPO_ROOT / "apps" / "invoice-ocr" / "backend" / "app" / "schemas" / "curation.py"
    text = schema.read_text()
    assert f'"{STATUS_INCLUDED}"' in text
    assert f'"{STATUS_EXCLUDED}"' in text


def test_status_wire_values_appear_in_frontend_curation_types():
    """STATUS_INCLUDED/EXCLUDED가 frontend `curation.ts`의 status 유니언과 같은지 고정한다.

    `blank_crop`도 Task 6에서 `CurationPairBase.exclusion_reason`에 추가됐으므로 함께
    고정한다 — 문자열이 갈라지면 자동 배제 배지(Task 7)가 서버 값을 인식하지 못한다.
    """
    types_file = _REPO_ROOT / "apps" / "invoice-ocr" / "frontend" / "src" / "types" / "curation.ts"
    text = types_file.read_text()
    assert f'"{STATUS_INCLUDED}"' in text
    assert f'"{STATUS_EXCLUDED}"' in text
    assert f'"{BLANK_CROP}"' in text


# --- is_machine_writable: §6 2×2 전수 (불변식의 고정점) ---


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        ("excluded", None, False),  # 사람이 배제 — 절대 건드리지 않는다
        ("excluded", BLANK_CROP, True),  # 기계가 배제, 사람 미개입 — 갱신 가능
        ("included", BLANK_CROP, False),  # 사람이 되돌림 — 영구 보호(오탐 관측치)
        ("included", None, True),  # 정상 후보 — 새로 배제 가능
    ],
)
def test_is_machine_writable_covers_all_four_cells(status, reason, expected):
    assert is_machine_writable(status, reason) is expected


def test_is_machine_writable_rejects_unknown_reason():
    # 미래에 사유 값이 늘어도 기계가 남의 판정을 덮지 않도록 fail-closed.
    assert is_machine_writable("excluded", "crop_defect") is False
    assert is_machine_writable("included", "crop_defect") is False


def test_is_machine_writable_rejects_unknown_status():
    assert is_machine_writable("pending", None) is False


# --- is_blank: 임계 경계 ---


def test_is_blank_is_true_at_and_below_threshold():
    assert is_blank(0.001, 0.01) is True
    assert is_blank(0.01, 0.01) is True  # 상한 '이하'가 빈 크롭


def test_is_blank_is_false_above_threshold():
    assert is_blank(0.011, 0.01) is False


def test_is_blank_is_false_for_nan():
    # NaN 비교는 항상 False → '빈 크롭 아님'으로 닫힌다. 오탐 0 우선(spec §7)과 같은 방향.
    assert is_blank(math.nan, 0.01) is False


# --- 임계 미확정 fail-fast ---


def test_require_blank_ink_max_raises_while_uncalibrated(monkeypatch):
    # None을 명시 주입한다 — 모듈 전역이 "지금 마침 None"인 것에 기대면, 운영에서 임계가
    # 확정되는 순간 이 테스트가 빨개져 "삭제하라"는 신호로 읽힌다(고정하려던 건 미확정
    # 상태의 fail-fast이지 미확정이라는 사실이 아니다). 값 주입 쪽 sibling과 대칭.
    monkeypatch.setattr("handwriting.blank_crop.BLANK_INK_MAX", None)
    with pytest.raises(RuntimeError, match="BLANK_INK_MAX"):
        require_blank_ink_max()


def test_require_blank_ink_max_returns_value_once_set(monkeypatch):
    monkeypatch.setattr("handwriting.blank_crop.BLANK_INK_MAX", 0.012)
    assert require_blank_ink_max() == 0.012


# --- crop_ink_ratio (cv2 글루) ---
# cv2가 없는 코어 venv에서는 이 절만 스킵된다(test_warp_gate.py:168 선례).

# 실 크롭 기하(실측): warp 캔버스 900×2100 고정(grid_v4.py:18) → φ그리드 pitch 81~83,
# 저장 크롭 폭 = ITEM_X(292) + 좌우 4px = 300. 저장 크롭 높이 중앙값 82(n=419).
# 이 기하에서 전치 커널 길이는 max(20, 82//3) = 27이다.
CROP_W, CROP_H = 300, 82


def _blank_crop():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2", exc_type=ImportError)
    return np.full((CROP_H, CROP_W, 3), 255, np.uint8)


def test_crop_ink_ratio_is_exactly_zero_for_white_crop():
    # 균일 캔버스는 국소대비가 0이라 잉크 마스크가 통째로 비고 답은 정확히 0.0이다(실측).
    # `< 0.005`로 두면 300×82 크롭에서 잉크 픽셀 122개까지 통과해 회귀를 놓친다.
    from handwriting.blank_crop import crop_ink_ratio

    assert crop_ink_ratio(_blank_crop()) == 0.0


def test_crop_ink_ratio_is_near_zero_for_printed_borders_only():
    # 회귀 고정: 크롭을 관통하는 좌우 세로 인쇄선 + 전폭 가로 인쇄선만 있는 크롭은
    # '빈 크롭'이어야 한다. 전치 패스가 없으면 0.030이 남아 빈칸군 p95(0.0253)를 넘는다.
    from handwriting.blank_crop import crop_ink_ratio

    img = _blank_crop()
    img[0:4, :] = 0  # 상 가로 인쇄선
    img[-4:, :] = 0  # 하 가로 인쇄선
    img[:, 0:5] = 0  # 좌 세로 인쇄선(전고)
    img[:, -5:] = 0  # 우 세로 인쇄선(전고)
    assert crop_ink_ratio(img) < 0.005


def _handwriting_crop():
    """글자 4개 흉내 — 각 글자는 세로획 2개(높이 22) + 가로획 2개, 획 두께 4."""
    img = _blank_crop()
    for gx in (40, 100, 160, 220):
        top = 28
        img[top : top + 22, gx : gx + 4] = 0
        img[top : top + 22, gx + 20 : gx + 24] = 0
        img[top : top + 4, gx : gx + 24] = 0
        img[top + 18 : top + 22, gx : gx + 24] = 0
    return img


def test_crop_ink_ratio_is_high_for_handwriting_strokes():
    from handwriting.blank_crop import crop_ink_ratio

    # 실측 정렬: 실 크롭 손글씨군의 p5가 0.0504, 최솟값 0.0406(n=190).
    assert crop_ink_ratio(_handwriting_crop()) > 0.04


def test_crop_ink_ratio_is_unchanged_by_adding_printed_lines():
    from handwriting.blank_crop import crop_ink_ratio

    plain = _handwriting_crop()
    lined = plain.copy()
    lined[0:4, :] = 0
    lined[-4:, :] = 0
    lined[:, 0:5] = 0
    lined[:, -5:] = 0
    assert crop_ink_ratio(lined) == pytest.approx(crop_ink_ratio(plain), abs=1e-6)


def test_crop_ink_ratio_rejects_zero_area_crop():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2", exc_type=ImportError)
    from handwriting.blank_crop import crop_ink_ratio

    with pytest.raises(ValueError):
        crop_ink_ratio(np.zeros((0, 10, 3), np.uint8))


def test_crop_ink_ratio_also_removes_tall_solid_vertical_bars_known_limit():
    """알려진 한계 — 크롭 높이의 1/3(=커널 27px) 이상인 '통짜' 세로 성분은 설계상 제거된다.

    _ink_mask는 국소대비라 통짜 막대를 꽉 찬 마스크로 만든다. 이 성질은 세로 인쇄선을
    지우기 위한 것이고, 실제 필체는 이런 형상을 만들지 않는다 — 실 운영 크롭 190건에서
    전치 패스의 잉크 잔존율은 최소 0.57이고 0.02 미만으로 붕괴한 건이 0건이다
    (근거: reviews/2026-07-30-receiving-review.md §1.2).
    합성 절벽 실측: 막대 높이 26px → 0.02114 보존 / 27px 이상 → 0.00000.
    """
    from handwriting.blank_crop import crop_ink_ratio

    img = _blank_crop()
    for gx in (40, 100, 160, 220):
        img[20:60, gx : gx + 5] = 0
    # 위 실측대로 답은 정확히 0.0이다 — 느슨한 상한은 "일부만 남았다"까지 통과시킨다.
    assert crop_ink_ratio(img) == 0.0
