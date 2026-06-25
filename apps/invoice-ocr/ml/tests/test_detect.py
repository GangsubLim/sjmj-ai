from ocr_poc.detect import DetectedCell, FakeDetector


def test_fake_detector_returns_seeded_cells():
    cells = [DetectedCell(0, 2, (10, 10, 20, 20)),
             DetectedCell(0, 3, (20, 10, 30, 20))]
    det = FakeDetector({"inv_x.jpg": cells})
    assert det.detect("/any/path/inv_x.jpg") == cells


def test_fake_detector_unknown_image_returns_empty():
    det = FakeDetector({})
    assert det.detect("/any/inv_none.jpg") == []
