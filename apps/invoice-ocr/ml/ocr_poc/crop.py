"""셀 박스 → 원해상도 crop. degenerate/경계밖 박스는 None(스킵)."""

from __future__ import annotations

from PIL import Image


def is_valid_bbox(bbox: tuple[float, float, float, float], img_w: int, img_h: int) -> bool:
    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        return False
    if x1 < 0 or y1 < 0 or x2 > img_w or y2 > img_h:
        return False
    return True


def crop_cell(image: Image.Image, bbox: tuple[float, float, float, float]) -> Image.Image | None:
    if not is_valid_bbox(bbox, image.width, image.height):
        return None
    x1, y1, x2, y2 = bbox
    return image.crop((int(x1), int(y1), int(x2), int(y2)))
