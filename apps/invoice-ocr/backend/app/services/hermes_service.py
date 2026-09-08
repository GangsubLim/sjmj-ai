"""HermesService — agent_uploads 산출물 ↔ 운영 DB 최종본 대조(읽기 전용 관찰).

파일 부재·손상의 HTTP 계약(spec §4.2)을 여기서 번역한다. agent_uploads·agent_knowledge
디렉터리를 **생성하지 않는다** — 읽기 전용이라 부재는 빈 결과이지 초기화 사유가 아니다
(ocr_service._upload_root가 mkdir하는 것과 의도적으로 다르다). 신선도는 요청 시 실시간
계산이다(spec §8.2) — 현 규모(초안 9건·16MB)에서 전량 스캔 비용이 사실상 0이다.
"""

import json
import re
from pathlib import Path

from app.config import data_root
from app.core.errors import AppError, not_found
from app.repositories.hermes_repository import HermesRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.services.hermes_diff import (
    Comparison,
    compare,
    mismatch_fields,
    norm,
    status_of,
    summarize,
    version_for,
)

UPLOAD_DIRNAME = "agent_uploads"
KNOWLEDGE_DIRNAME = "agent_knowledge"

# 이 패턴에 걸리는 파일의 존재가 "hermes 경로로 들어온 건"의 정의다 — ML 경로 건과 자연
# 분리된다(spec §3.1). ml/tools/agent_report._DRAFT_RE와 같은 규칙.
_DRAFT_RE = re.compile(r"^(\d+)\.draft\.json$")
_PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png")

STATUSES = ("deleted", "match", "mismatch")


def _read_json(path: Path) -> dict:
    """산출물 JSON을 읽는다. 파싱 실패는 삼키지 않고 500으로 드러낸다(운영 이상 신호)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AppError(500, "SERVER_ERROR", f"산출물 JSON 파싱 실패: {path.name}") from e


class HermesService:
    """hermes 위임 입력 현황 도메인 서비스(읽기 전용)."""

    def __init__(self, repo=None, invoice_repo=None):
        """저장소를 주입받아 초기화한다(미지정 시 기본 구현).

        상세는 건 1개라 N+1이 성립하지 않으므로 bulk 저장소가 아니라 InvoiceRepository의
        find_by_id/find_items를 재사용한다 — 그 둘이 SELECT *라 상세가 요구하는
        quantity·unit·unit_price·deduction이 신규 SQL 없이 그대로 들어온다(spec §5).
        """
        self.repo = repo or HermesRepository()
        self.invoice_repo = invoice_repo or InvoiceRepository()

    # --- 경로 ---

    def _uploads(self) -> Path:
        return data_root() / UPLOAD_DIRNAME

    def _knowledge(self) -> Path:
        return data_root() / KNOWLEDGE_DIRNAME

    def _photo(self, invoice_id: int) -> Path | None:
        root = self._uploads()
        for suffix in _PHOTO_SUFFIXES:
            candidate = root / f"{invoice_id}{suffix}"
            if candidate.is_file():
                return candidate
        return None

    # --- 스캔 ---

    def _load_drafts(self) -> list[tuple[int, dict]]:
        """{id}.draft.json만 골라 id 오름차순으로 읽는다(그 외 파일은 무시)."""
        root = self._uploads()
        if not root.is_dir():
            return []
        out = []
        for p in root.iterdir():
            m = _DRAFT_RE.match(p.name)
            if m:
                out.append((int(m.group(1)), _read_json(p)))
        return sorted(out, key=lambda d: d[0])

    def _scan(self) -> list[dict]:
        """초안 전량을 최종본과 조인해 건별 대조 결과까지 붙인다.

        Returns:
            [{id, draft, final|None, comparison|None}] — id 오름차순.
        """
        drafts = self._load_drafts()
        finals = self.repo.find_finals([jid for jid, _ in drafts])
        out = []
        for jid, body in drafts:
            final = finals.get(jid)
            out.append(
                {
                    "id": jid,
                    "draft": body,
                    "final": final,
                    "comparison": compare(body, final) if final is not None else None,
                }
            )
        return out

    # --- 지식 상태 ---

    def _load_versions(self) -> list[dict]:
        """versions.jsonl 전량(발행·거부 기록 모두). 부재는 빈 목록."""
        path = self._knowledge() / "versions.jsonl"
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _count_corrections(self) -> int:
        """corrections.jsonl 누적 줄 수. 부재는 0."""
        path = self._knowledge() / "corrections.jsonl"
        if not path.is_file():
            return 0
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())

    def _knowledge_state(self, versions: list[dict]) -> dict:
        published = [v for v in versions if "rejected" not in v]
        latest = max(published, key=lambda v: v["version"], default=None)
        return {
            "version": latest["version"] if latest else 0,
            "published_at": latest["published_at"] if latest else None,
            "corrections": self._count_corrections(),
        }

    # --- 공개 API ---

    def summary(self) -> dict:
        """초안 전량의 집계·판독 지식 상태·지식 버전별 일치율을 조회한다.

        Returns:
            {totals, knowledge, by_version}. totals·by_version에서 edited 키는
            타임스탬프 파생이라 떼어낸다(spec §3.2).
        """
        scanned = self._scan()
        rows = [(e["id"], e["comparison"]) for e in scanned if e["comparison"] is not None]
        missing = len(scanned) - len(rows)
        totals = _without_edited(summarize(rows, missing=missing))

        versions = self._load_versions()
        groups: dict[int, list[tuple[int, Comparison]]] = {}
        for e in scanned:
            if e["comparison"] is None:
                # 최종본이 없으면 created_at이 없어 버전 구간에 매핑할 수 없다.
                continue
            key = version_for(e["final"]["created_at"], versions)
            groups.setdefault(key, []).append((e["id"], e["comparison"]))
        by_version = [
            {"version": v, **_without_edited(summarize(groups[v]))} for v in sorted(groups)
        ]
        return {
            "totals": totals,
            "knowledge": self._knowledge_state(versions),
            "by_version": by_version,
        }

    def list_entries(
        self, page: int, limit: int, status: str | None = None
    ) -> tuple[list[dict], int]:
        """초안 목록을 상태 필터·페이지로 조회한다.

        필터는 목록과 total을 같은 조건으로 좁힌다 — 필터를 켠 화면의 총건수가 그대로
        "남은 일"이 되게 하기 위함이다(curation의 row_delta와 같은 계약).

        Args:
            page: 1부터. 라우터가 clamp한 값이 온다.
            limit: 페이지 크기. 라우터가 clamp한 값이 온다.
            status: deleted/match/mismatch 중 하나 또는 None(전체). 라우터가 검증한다.

        Returns:
            (엔트리 목록, 필터 적용 후 총건수).
        """
        entries = [self._entry(e) for e in self._scan()]
        if status is not None:
            entries = [e for e in entries if e["status"] == status]
        # id 내림차순 — 방금 들어온 건이 맨 위다.
        entries.sort(key=lambda e: e["id"], reverse=True)
        total = len(entries)
        start = (page - 1) * limit
        return entries[start : start + limit], total

    def _entry(self, scanned: dict) -> dict:
        """스캔 1건을 목록 행 DTO로 좁힌다.

        발행일은 초안값·최종값을 나란히 싣기만 하고 상태 판정에 넣지 않는다 — hermes
        스킬이 발행일을 판독하지 않고 항상 오늘로 채우므로, 불일치로 세면 사실상 전건이
        mismatch가 되어 상태 축이 붕괴한다(spec §3.2).
        """
        draft, final, comparison = scanned["draft"], scanned["final"], scanned["comparison"]
        invoice_id = scanned["id"]
        return {
            "id": invoice_id,
            "issue_date_draft": draft.get("issue_date"),
            "issue_date_final": final["issue_date"] if final else None,
            "recipient_draft": draft.get("recipient"),
            "recipient_final": final["recipient"] if final else None,
            "item_count_draft": len(draft.get("items") or []),
            "item_count_final": len(final["items"]) if final else None,
            "grand_total_draft": draft.get("grand_total"),
            "grand_total_final": final["grand_total"] if final else None,
            "status": status_of(comparison),
            "mismatch_fields": mismatch_fields(comparison) if comparison else [],
            "has_photo": self._photo(invoice_id) is not None,
            "has_raw": (self._uploads() / f"{invoice_id}.raw.json").is_file(),
        }

    def get_entry(self, invoice_id: int) -> dict:
        """초안 1건의 전사값·초안·최종본 3단 대조를 조회한다.

        Args:
            invoice_id: 초안 파일명의 id.

        Returns:
            {id, status, draft, final|None, rows[], has_photo, mismatch_fields}.

        Raises:
            AppError: 초안 파일 부재 404, 초안·raw.json 파싱 실패 500.
        """
        draft_path = self._uploads() / f"{invoice_id}.draft.json"
        if not draft_path.is_file():
            not_found("hermes 초안을 찾을 수 없습니다.")
        draft = _read_json(draft_path)

        header = self.invoice_repo.find_by_id(invoice_id)
        final_items = self.invoice_repo.find_items(invoice_id) if header else []
        comparison = (
            compare(draft, {**header, "items": final_items}) if header is not None else None
        )
        return {
            "id": invoice_id,
            "status": status_of(comparison),
            "draft": draft,
            "final": header,
            "rows": _build_rows(draft.get("items") or [], final_items, self._load_raw(invoice_id)),
            "has_photo": self._photo(invoice_id) is not None,
            "mismatch_fields": mismatch_fields(comparison) if comparison else [],
        }

    def _load_raw(self, invoice_id: int) -> list[dict] | None:
        """1단계 패스1 전사값. 파일 부재는 None(열 자체를 숨기는 신호)."""
        path = self._uploads() / f"{invoice_id}.raw.json"
        if not path.is_file():
            return None
        return _read_json(path).get("rows") or []

    def photo_path(self, invoice_id: int) -> str:
        """원본 사진의 절대경로를 반환한다.

        Raises:
            AppError: 초안 부재 또는 사진 파일 부재 404.
        """
        if not (self._uploads() / f"{invoice_id}.draft.json").is_file():
            not_found("hermes 초안을 찾을 수 없습니다.")
        path = self._photo(invoice_id)
        if path is None:
            not_found("원본 사진이 없습니다.")
        return str(path)


def _without_edited(s: dict) -> dict:
    """집계에서 edited를 떼어낸다 — 계산은 픽스처 동치용이고 노출은 하지 않는다(spec §3.2)."""
    return {k: v for k, v in s.items() if k != "edited"}


def _item(row: dict | None) -> dict | None:
    """행 1개를 대조 표에 실을 6열로 좁힌다(짝이 없는 쪽은 None)."""
    if row is None:
        return None
    return {
        "name": row.get("name"),
        "quantity": row.get("quantity"),
        "unit": row.get("unit"),
        "unit_price": row.get("unit_price"),
        "supply": row.get("supply"),
        # MySQL BOOLEAN은 0/1 정수로 돌아온다 — 초안의 bool과 같은 타입으로 맞춘다.
        "deduction": bool(row.get("deduction")),
    }


def _row_mismatch(draft_item: dict | None, final_item: dict | None) -> list[str]:
    """행 1개의 셀 강조 축. 짝이 없으면 빈 목록 — 항목 수 불일치로 이미 드러난다."""
    if draft_item is None or final_item is None:
        return []
    out = []
    if norm(draft_item.get("name")) != norm(final_item.get("name")):
        out.append("name")
    if int(draft_item.get("supply") or 0) != int(final_item.get("supply") or 0):
        out.append("supply")
    return out


def _build_rows(
    draft_items: list[dict], final_items: list[dict], raw_rows: list[dict] | None
) -> list[dict]:
    """전사값·초안·최종본을 index로 짝지어 행별 3열을 만든다.

    길이는 max(초안, 최종본)이다 — 초안 길이로 자르면 사람이 **추가한** 행이 통째로
    사라져, 항목 수 불일치를 1급 상태로 두는 화면이 그 불일치의 실물을 못 보여준다.
    raw_rows는 SKILL.md 계약상 초안 items와 순서·개수 1:1이지만 길이가 어긋나도
    죽지 않고 없는 쪽을 None으로 남긴다(보관 전용 산출물이라 백엔드 검증 대상이 아니다).
    """
    rows = []
    for i in range(max(len(draft_items), len(final_items))):
        d = draft_items[i] if i < len(draft_items) else None
        f = final_items[i] if i < len(final_items) else None
        r = raw_rows[i] if raw_rows is not None and i < len(raw_rows) else None
        rows.append(
            {
                "index": i,
                "raw": {"text": r.get("raw"), "conf": r.get("conf")} if r else None,
                "draft": _item(d),
                "final": _item(f),
                "mismatch": _row_mismatch(d, f),
            }
        )
    return rows
