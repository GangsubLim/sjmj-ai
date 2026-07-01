"""셀 박스 → 원해상도 crop. degenerate/경계밖 박스는 None(스킵)."""

from __future__ import annotations

from PIL import Image


def is_valid_bbox(bbox: tuple[float, float, float, float], img_w: int, img_h: int) -> bool:
    """박스가 비퇴화이며 이미지 경계 안에 있는지 검사한다."""
    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        return False
    return not (x1 < 0 or y1 < 0 or x2 > img_w or y2 > img_h)


def crop_cell(image: Image.Image, bbox: tuple[float, float, float, float]) -> Image.Image | None:
    """박스 영역을 원해상도로 crop한다. 유효하지 않은 박스는 None."""
    if not is_valid_bbox(bbox, image.width, image.height):
        return None
    x1, y1, x2, y2 = bbox
    return image.crop((int(x1), int(y1), int(x2), int(y2)))
