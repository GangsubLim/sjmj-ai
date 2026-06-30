"""공용 픽스처. 실데이터에 의존하지 않는 합성 데이터만 둔다."""

import pytest


@pytest.fixture
def tiny_invoices_sql() -> str:
    """invoices/invoice_items 최소 INSERT 샘플 (백업 형식 모사)."""
    return (
        "INSERT INTO `invoices` (`id`, `document_title`, `issue_date`, `recipient`, "
        "`recipient2`, `vehicle_no`, `memo`, `show_stamp`, `issuer_id`, `total_supply`, "
        "`total_vat`, `grand_total`, `created_at`, `updated_at`) VALUES\n"
        "(11, '거래명세서', '2026-05-12', '옥천운수', '이희원', '5608', '', 1, NULL, "
        "300000, 30000, 330000, '2026-05-12 05:57:39', '2026-05-12 05:57:39'),\n"
        "(12, '거래명세서', '2026-05-13', '성우항공', NULL, '3102', 'O''Brien 메모', 1, NULL, "
        "120000, 12000, 132000, '2026-05-13 08:48:53', '2026-05-13 08:48:53');\n"
        "INSERT INTO `invoice_items` (`id`, `invoice_id`, `item_order`, `name`, `quantity`, "
        "`unit`, `unit_price`, `supply`, `vat`, `total`) VALUES\n"
        "(42, 11, 1, '단지', 1, 'EA', 300000, 300000, 30000, 330000),\n"
        "(43, 12, 1, '세차', 1, 'EA', 30000, 30000, 3000, 33000),\n"
        "(44, 12, 2, '중고타이어', 1, NULL, 90000, 90000, 9000, 99000);\n"
    )
