"""표인식 환경·셀검출 스파이크 (일회성, paddleocr 3.x).

usage: uv run python -m tools.spike_ppstructure inv_003   (cwd = apps/invoice-ocr/ml)
한 장에 표인식을 돌려 (1) 환경이 서는지 (2) 셀 박스가 쓸만하게 잡히는지를
raw 구조 덤프 + 시각화로 확인한다. 결과로 detect.py 의 DetectorAdapter 가
3.x 출력에서 무엇을 어떻게 뽑을지 확정한다.

paddleocr 2.x 의 PPStructure 는 3.x 에서 제거됐다. 3.x 표인식은
TableRecognitionPipelineV2(표 검출 → 셀 검출 → 구조인식)로 대체됐다.
"""
import sys
from pathlib import Path

from ocr_poc import config


def main(image_id: str) -> None:
    img_path = config.images_dir() / f"{image_id}.jpg"
    print(f"[spike] image = {img_path}")

    from paddleocr import TableRecognitionPipelineV2  # noqa: PLC0415

    pipeline = TableRecognitionPipelineV2()
    out_dir = Path("report")
    out_dir.mkdir(parents=True, exist_ok=True)

    results = list(pipeline.predict(str(img_path)))
    print(f"[spike] result objects = {len(results)}")
    for i, res in enumerate(results):
        data = getattr(res, "json", None)
        if isinstance(data, dict):
            payload = data.get("res", data)
            print(f"[spike] result[{i}] keys = {list(payload.keys())}")
            _dump_table_summary(payload)
        # 주석 오버레이·json 자동 저장 (시각 게이트용)
        res.save_to_img(str(out_dir))
        res.save_to_json(str(out_dir))
    print(f"[spike] overlays/json saved → {out_dir}")


def _dump_table_summary(payload: dict) -> None:
    """셀 박스 개수·구조 길이 등 게이트 판단에 필요한 수치만 덤프."""
    tables = payload.get("table_res_list") or payload.get("table_recognition_result") or []
    print(f"[spike]   tables = {len(tables)}")
    for ti, t in enumerate(tables):
        if not isinstance(t, dict):
            continue
        cells = t.get("cell_box_list") or t.get("cell_bbox") or []
        html = t.get("pred_html") or t.get("html") or ""
        print(f"[spike]   table[{ti}] cells = {len(cells)}  html_len = {len(html)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "inv_003")
