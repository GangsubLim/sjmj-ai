"""PP-Structure 환경·표인식 스파이크 (일회성).

usage: uv run python -m tools.spike_ppstructure inv_003   (cwd = apps/invoice-ocr/ml)
한 장에 PP-Structure 표인식을 돌려 (1) 환경이 서는지 (2) 셀 박스가
쓸만하게 잡히는지를 raw 출력 덤프 + 시각화로 확인한다. 결과로 detect.py의
DetectorAdapter가 PP-Structure 출력에서 무엇을 어떻게 뽑을지 확정한다.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from ocr_poc import config


def main(image_id: str) -> None:
    img_path = config.images_dir() / f"{image_id}.jpg"
    print(f"[spike] image = {img_path}")

    # PP-Structure 호출 — 실제 API 시그니처는 설치된 paddleocr 버전에서 확인.
    # paddleocr 2.7 계열: from paddleocr import PPStructure
    from paddleocr import PPStructure  # noqa: PLC0415

    engine = PPStructure(show_log=False, lang="korean")
    result = engine(str(img_path))

    print(f"[spike] result blocks = {len(result)}")
    overlay = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(overlay)
    for block in result:
        print("  block.type =", block.get("type"), " bbox =", block.get("bbox"))
        res = block.get("res")
        # table 블록이면 res에 cell 박스/html이 들어옴 — 구조를 그대로 덤프.
        if isinstance(res, dict):
            print("    res.keys =", list(res.keys()))
            for cell in res.get("cell_bbox", []) or []:
                draw.polygon([tuple(p) for p in _as_points(cell)], outline=(255, 0, 0))
    out = Path("report") / f"spike-{image_id}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(out)
    print(f"[spike] overlay saved → {out}")


def _as_points(cell):
    """cell_bbox 항목을 (x,y) 점 리스트로. [x1,y1,x2,y2] 또는 8-좌표 모두 수용."""
    flat = list(cell)
    if len(flat) == 4:
        x1, y1, x2, y2 = flat
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "inv_003")
