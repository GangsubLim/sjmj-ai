import pytest

from app.services.export_service import ExportService, sanitize_csv_field


def test_sanitize_blocks_formula_injection():
    assert sanitize_csv_field("=1+1") == "'=1+1"
    assert sanitize_csv_field("+82") == "'+82"
    assert sanitize_csv_field("-5") == "'-5"
    assert sanitize_csv_field("@x") == "'@x"


def test_sanitize_passes_safe_and_korean_and_none():
    assert sanitize_csv_field("한양운수") == "한양운수"
    assert sanitize_csv_field("12가3456") == "12가3456"
    assert sanitize_csv_field(None) == ""


def test_export_rejects_non_csv():
    with pytest.raises(ValueError, match="현재 CSV 형식만 지원합니다."):
        ExportService(repo=_StubRepo([])).export_invoices("xlsx", {})


def test_export_returns_filename_and_bom_csv():
    repo = _StubRepo(
        [
            {
                "id": 1,
                "document_title": "거 래 명 세 서",
                "issue_date": "2026-05-15",
                "recipient": "=evil",
                "recipient2": "",
                "vehicle_no": "12가3456",
                "memo": None,
                "total_supply": 100000,
                "total_vat": 10000,
                "grand_total": 110000,
                "created_at": "2026-05-15 10:00:00",
            }
        ]
    )
    filename, body = ExportService(repo=repo).export_invoices("csv", {})
    assert filename.startswith("거래명세서_") and filename.endswith(".csv")
    assert body.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    text = body.decode("utf-8")
    assert "ID,문서제목,발행일" in text  # 헤더 행
    assert "'=evil" in text  # formula injection sanitize


class _StubRepo:
    def __init__(self, rows):
        self._rows = rows

    def find_all_for_export(self, filters):
        return self._rows
