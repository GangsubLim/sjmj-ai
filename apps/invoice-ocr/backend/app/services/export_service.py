"""ExportService — PHP services/ExportService.php 동형(CSV + formula injection 방지).

설계 차이: PHP는 php://output 직접 출력. FastAPI는 (filename, csv_bytes)를 반환하고
라우터가 envelope 밖 Response(text/csv)로 흘린다(테스트 가능·envelope 미오염).
"""
import csv
import io
import re
from datetime import date

from app.repositories.invoice_repository import InvoiceRepository

_FORMULA = re.compile(r"^[=+\-@\t\r]")
_HEADER = ["ID", "문서제목", "발행일", "거래처", "거래처2", "차량번호", "메모",
           "공급가액", "부가세", "합계", "생성일"]


def sanitize_csv_field(value) -> str:
    """=,+,-,@,TAB,CR로 시작하는 값 앞에 작은따옴표를 붙여 수식 해석을 막는다."""
    s = "" if value is None else str(value)
    if s != "" and _FORMULA.match(s):
        return "'" + s
    return s


class ExportService:
    def __init__(self, repo: InvoiceRepository | None = None):
        self.repo = repo or InvoiceRepository()

    def export_invoices(self, format: str, filters: dict) -> tuple[str, bytes]:
        if format != "csv":
            raise ValueError("현재 CSV 형식만 지원합니다.")
        invoices = self.repo.find_all_for_export(filters)
        filename = f"거래명세서_{date.today().isoformat()}.csv"

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_HEADER)
        for inv in invoices:
            writer.writerow([
                inv["id"],
                sanitize_csv_field(inv["document_title"]),
                inv["issue_date"],
                sanitize_csv_field(inv["recipient"]),
                sanitize_csv_field(inv["recipient2"]),
                sanitize_csv_field(inv["vehicle_no"]),
                sanitize_csv_field(inv.get("memo") or ""),
                inv["total_supply"], inv["total_vat"], inv["grand_total"], inv["created_at"],
            ])
        body = b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")  # UTF-8 BOM(Excel 한글 호환)
        return filename, body
