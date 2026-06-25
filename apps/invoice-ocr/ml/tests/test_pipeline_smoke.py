from PIL import Image

from ocr_poc.data import Sample, LabelRow
from ocr_poc.db import InvoiceItem
from ocr_poc.detect import DetectedCell, FakeDetector
from ocr_poc.recognize import FakeRecognizer
from ocr_poc.match import GroundTruth
from ocr_poc.__main__ import run_pipeline


def test_run_pipeline_end_to_end_with_fakes(tmp_path):
    # 1행 명세서: 수량4 단가25000 공급가100000, 표 헤더 + 1 데이터행
    sample = Sample(image_id="inv_x", image_path=tmp_path / "inv_x.jpg",
                    label_rows=(LabelRow(1, "부동액", "4", "25000", "100000"),))
    Image.new("RGB", (200, 100), (255, 255, 255)).save(sample.image_path)

    # 검출: 헤더행(row0) 3열 + 데이터행(row1) 3열
    cells = [
        DetectedCell(0, 1, (10, 0, 20, 10)), DetectedCell(0, 2, (20, 0, 30, 10)),
        DetectedCell(0, 3, (30, 0, 40, 10)),
        DetectedCell(1, 1, (10, 20, 20, 30)), DetectedCell(1, 2, (20, 20, 30, 30)),
        DetectedCell(1, 3, (30, 20, 40, 30)),
    ]
    detector = FakeDetector({"inv_x.jpg": cells})
    # 인식 순서: 헤더(수량/단가/공급가) → 데이터(4 / 25 / 100)
    recognizer = FakeRecognizer(["수량", "단가", "공급가", "4", "25", "100"])

    gt = {"inv_x": GroundTruth("inv_x", 1, (
        InvoiceItem(1, 1, "부동액", 4, 25000, 100000, 10000, 110000),))}

    scores, report_data = run_pipeline([sample], gt, detector, recognizer, Image.open)

    assert len(scores) == 1
    # 천원곱으로 25→25000, 100→100000 정규화되어 전부 정답
    assert report_data.metrics["detection_recall"] == 1.0
    assert report_data.metrics["recognition_accuracy"] == 1.0
    assert report_data.metrics["row_exact_rate"] == 1.0
