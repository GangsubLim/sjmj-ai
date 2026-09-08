"""hermes 관점의 최종본 bulk 조회. text() raw SQL 직접 발행.

목록·집계는 초안 전량을 한 방으로 읽는다 — 건마다 조회하면 N+1이다. 상세(건 1개)는 이
저장소를 쓰지 않고 InvoiceRepository.find_by_id/find_items를 재사용한다(spec §5).
"""

from sqlalchemy import bindparam, text

from app.db import connection

# 대조 축은 ml/tools/agent_report.py의 FINALS_SQL과 같다 — 공유 골든 픽스처 동치의 전제라
# 여기서 열을 늘리거나 줄이면 두 구현의 입력이 갈린다. issue_date만 더 읽는데, 이 열은
# compare가 보지 않는(발행일은 대조 축이 아니다, spec §3.2) 목록 표시 전용이다.
_FINALS_SQL = text("""
    SELECT i.id, i.issue_date, i.recipient, i.grand_total, i.created_at, i.updated_at,
           t.item_order, t.name, t.supply
    FROM invoices i
    LEFT JOIN invoice_items t ON t.invoice_id = i.id
    WHERE i.id IN :ids
    ORDER BY i.id, t.item_order
""").bindparams(bindparam("ids", expanding=True))


def _group(rows: list[dict]) -> dict[int, dict]:
    """조인 행을 invoice id별 최종본으로 묶는다(items는 SQL이 이미 item_order로 정렬)."""
    out: dict[int, dict] = {}
    for r in rows:
        inv = out.setdefault(
            r["id"],
            {
                "issue_date": r["issue_date"],
                "recipient": r["recipient"],
                "grand_total": r["grand_total"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "items": [],
            },
        )
        # 품목 0건이면 LEFT JOIN이 NULL 행 하나를 낸다 — 그 행은 items에 넣지 않는다.
        if r["item_order"] is not None:
            inv["items"].append(
                {"item_order": r["item_order"], "name": r["name"], "supply": r["supply"]}
            )
    return out


class HermesRepository:
    """초안 id 목록에 대응하는 최종본(invoices + invoice_items) 데이터 접근 계층."""

    def find_finals(self, ids: list[int]) -> dict[int, dict]:
        """초안 id 목록의 최종본을 id별 dict로 묶어 반환한다(없는 id는 결과에서 빠진다).

        Args:
            ids: 초안 파일명에서 뽑은 invoice id 목록.

        Returns:
            {invoice id: {issue_date, recipient, grand_total, created_at, updated_at, items[]}}.
        """
        if not ids:
            return {}
        with connection() as conn:
            rows = [dict(m) for m in conn.execute(_FINALS_SQL, {"ids": ids}).mappings().all()]
        return _group(rows)
