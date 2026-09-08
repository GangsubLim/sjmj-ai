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
from app.core.errors import AppError
from app.repositories.hermes_repository import HermesRepository
from app.services.hermes_diff import (
    Comparison,
    compare,
    mismatch_fields,
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

    def __init__(self, repo=None):
        """저장소를 주입받아 초기화한다(미지정 시 기본 구현)."""
        self.repo = repo or HermesRepository()

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


def _without_edited(s: dict) -> dict:
    """집계에서 edited를 떼어낸다 — 계산은 픽스처 동치용이고 노출은 하지 않는다(spec §3.2)."""
    return {k: v for k, v in s.items() if k != "edited"}
