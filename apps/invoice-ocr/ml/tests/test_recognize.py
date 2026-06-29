from ocr_poc.recognize import numeric_postfilter, FakeRecognizer


def test_numeric_postfilter_strips_non_digits():
    assert numeric_postfilter("₩25,000") == "25000"
    assert numeric_postfilter("o3o oo0") == "30"   # 오인식 문자 사이 숫자만(3,0)
    assert numeric_postfilter("없음") == ""
    assert numeric_postfilter("100 000") == "100000"


def test_fake_recognizer_returns_seeded_text():
    rec = FakeRecognizer(["25000", "", "100000"])
    assert rec.recognize(object()) == "25000"
    assert rec.recognize(object()) == ""
    assert rec.recognize(object()) == "100000"
