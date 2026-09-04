"""geometry.py — 단계 기하 문서 조립·원자적 기록(순수 계층, stdlib만).

cv2/numpy 없이 도는 것이 계약이다 — 코어 paddle-free venv에서도 이 파일 전량이 실행된다.
"""

import json
from dataclasses import dataclass

import pytest

from handwriting.geometry import (
    GEOMETRY_FILENAME,
    GEOMETRY_VERSION,
    build_geometry,
    row_geometry,
    write_geometry,
)


@dataclass(frozen=True)
class _Row:
    """group.Row의 geometry 관련 3필드만 흉내 낸 대역."""

    band: tuple
    rtype: str
    box: tuple | None


def test_row_index_follows_the_crop_predicate_not_the_row_type():
    """row_index는 '크롭된 new 행'에만 붙는다 — block_amounts의 술어와 글자 그대로 같다.

    box 없는 new 행은 크롭도 학습쌍도 만들어지지 않으므로 row-{i}.png의 i를 소비하지 않는다.
    여기서 한 칸 어긋나면 사람이 다른 줄을 보고 판정한다(spec 불변식 6).
    """
    rows = row_geometry(
        [
            _Row((100, 180), "new", (105, 175)),
            _Row((180, 260), "cont", None),
            _Row((260, 340), "new", None),  # box 없음 — 크롭 대상 아님
            _Row((340, 420), "new", (345, 415)),
            _Row((420, 500), "total", (425, 495)),
        ]
    )

    assert [r["row_index"] for r in rows] == [0, None, None, 1, None]
    assert [r["type"] for r in rows] == ["new", "cont", "new", "new", "total"]


def test_item_box_is_only_the_actually_cropped_box():
    """total 행은 box를 갖지만 크롭되지 않는다 — item_box는 실제 크롭 박스만 가리킨다."""
    rows = row_geometry([_Row((0, 80), "total", (5, 75)), _Row((80, 160), "new", (85, 155))])

    assert rows[0]["item_box"] is None
    assert rows[1]["item_box"] == [85, 155]


def test_bands_and_boxes_are_plain_ints():
    """json.dumps가 통과해야 한다 — 상류 좌표는 numpy 스칼라일 수 있다."""
    rows = row_geometry([_Row((10, 20), "new", (11, 19))])

    assert rows[0]["band"] == [10, 20]
    json.dumps(rows)


def test_partial_document_omits_stages_never_reached():
    """강등 잡은 쿼드·deskew까지만 간다 — 하류 키를 null로 채우지 않고 아예 넣지 않는다.

    부재와 null은 다른 말이다. null이면 "행을 검출했는데 비어 있다"로 읽힌다.
    """
    doc = build_geometry(
        generation=2,
        image_size=(4032, 3024),
        warp_size=(900, 2100),
        quad=[(0, 0), (10, 0), (10, 20), (0, 20)],
        quad_source="color",
        deskew_deg=0.42,
    )

    assert doc["version"] == GEOMETRY_VERSION
    assert doc["generation"] == 2
    assert doc["image_size"] == [4032, 3024]
    assert doc["warp_size"] == [900, 2100]
    assert doc["quad"] == [[0.0, 0.0], [10.0, 0.0], [10.0, 20.0], [0.0, 20.0]]
    assert doc["quad_source"] == "color"
    assert doc["deskew_deg"] == pytest.approx(0.42)
    assert "hlines" not in doc
    assert "rows" not in doc
    assert "pitch" not in doc


def test_full_document_carries_the_values_actually_used_by_this_job():
    """item_x·amount_x는 템플릿 상수가 아니라 그 잡에 실제로 쓰인 값이다(#50)."""
    doc = build_geometry(
        generation=0,
        image_size=(4032, 3024),
        warp_size=(900, 2100),
        quad=[(0, 0), (1, 0), (1, 1), (0, 1)],
        quad_source="dl",
        deskew_deg=-0.1,
        hlines=[614, 696, 778],
        pitch=82.3,
        item_x=(96, 396),
        amount_x=(630, 896),
        rows=row_geometry([_Row((612, 694), "new", (618, 690))]),
    )

    assert doc["hlines"] == [614, 696, 778]
    assert doc["pitch"] == pytest.approx(82.3)
    assert doc["item_x"] == [96, 396]
    assert doc["amount_x"] == [630, 896]
    assert doc["rows"][0]["row_index"] == 0
    json.dumps(doc)


def test_write_is_atomic_and_leaves_the_previous_state_on_failure(tmp_path):
    """직렬화 도중 죽어도 잘린 파일이 남지 않는다 — 직전 상태(이전 완전 문서)가 그대로다.

    tmp에 덤프한 뒤 os.replace로 갈아끼우므로, 실패는 target에 닿지 않는다. 기록 실패를
    삼키는 이상(추론을 죽이지 않아야 한다) 이 원자성이 없으면 잘린 JSON이 노출되고,
    그 tmp_dir이 _swap_crop_dir로 교체되면 영속된다.
    """
    good = build_geometry(generation=0, image_size=(1, 1), warp_size=(900, 2100))
    assert write_geometry(tmp_path, good) is True

    class _Unserializable:
        pass

    assert write_geometry(tmp_path, {"bad": _Unserializable()}) is False

    target = tmp_path / GEOMETRY_FILENAME
    assert json.loads(target.read_text(encoding="utf-8")) == good
    assert list(tmp_path.glob("*.tmp")) == [], "임시 파일이 남으면 교체 후 그대로 노출된다"


def test_write_failure_is_swallowed_and_reported(tmp_path, capsys):
    """기록 실패가 추론을 죽이지 않는다 — 기하는 진단이지 산출물이 아니다."""
    missing = tmp_path / "nope" / "deeper"

    assert write_geometry(missing / "\x00bad", {"a": 1}) is False
    assert "[geometry]" in capsys.readouterr().err
