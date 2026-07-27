import pytest

from app.core.errors import AppError
from app.services.ocr_service import _validated_suffix


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("a.jpg", ".jpg"),
        ("a.jpeg", ".jpeg"),
        ("a.png", ".png"),
        ("A.PNG", ".png"),
        ("IMG_0001.JPG", ".jpg"),
        ("사진.Jpeg", ".jpeg"),
    ],
)
def test_returns_lowercased_suffix_for_allowed_extensions(filename, expected):
    assert _validated_suffix(filename) == expected


@pytest.mark.parametrize("filename", ["photo", "", "x.sh", "x.php", "x.jpg.sh", "x.webp"])
def test_rejects_missing_or_disallowed_suffix(filename):
    with pytest.raises(AppError) as ei:
        _validated_suffix(filename)
    assert ei.value.status == 400
    assert ei.value.code == "VALIDATION_ERROR"
    assert ei.value.details == {"photo": "jpg/jpeg/png 확장자만 업로드할 수 있습니다."}


@pytest.mark.parametrize(
    "filename", ["x'\n.jpg", "x\x00.jpg", "x\x1f.jpg", "x\x7f.jpg", "\x00.png"]
)
def test_rejects_control_characters_anywhere_in_filename(filename):
    with pytest.raises(AppError) as ei:
        _validated_suffix(filename)
    assert ei.value.status == 400
    assert ei.value.code == "VALIDATION_ERROR"
    assert ei.value.details == {"photo": "파일명에 제어문자를 사용할 수 없습니다."}


def test_control_char_check_precedes_suffix_check():
    """제어문자 + 허용 확장자 조합은 제어문자 메시지로 거부돼야 한다(검사 순서 고정)."""
    with pytest.raises(AppError) as ei:
        _validated_suffix("scan\x07.png")
    assert ei.value.details == {"photo": "파일명에 제어문자를 사용할 수 없습니다."}
