from PIL import Image

from ocr_poc.crop import crop_cell, is_valid_bbox


def test_is_valid_bbox_rejects_degenerate_and_oob():
    assert is_valid_bbox((10, 10, 20, 20), 100, 100) is True
    assert is_valid_bbox((20, 10, 10, 20), 100, 100) is False  # x2<x1
    assert is_valid_bbox((10, 10, 10, 20), 100, 100) is False  # 폭 0
    assert is_valid_bbox((-1, 10, 20, 20), 100, 100) is False  # 경계밖
    assert is_valid_bbox((10, 10, 20, 200), 100, 100) is False  # 경계밖


def test_crop_cell_returns_subimage():
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    out = crop_cell(img, (10, 20, 40, 50))
    assert out is not None
    assert out.size == (30, 30)


def test_crop_cell_none_on_invalid():
    img = Image.new("RGB", (100, 100))
    assert crop_cell(img, (40, 20, 10, 50)) is None
