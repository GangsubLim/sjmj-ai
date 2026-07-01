"""CurationService — 검수 큐/잡 상세/쌍 큐레이션/검수완료/이미지 경로 해석.

라우터(HTTP)와 repository(SQL) 사이의 정규화·비즈니스 로직 계층.
"""

import os
from pathlib import Path

from app.core.errors import not_found
from app.repositories.curation_repository import CurationRepository


class CurationService:
    """큐레이션 도메인 서비스."""

    def __init__(self, repo=None):
        """저장소를 주입받아 초기화한다(미지정 시 기본 구현)."""
        self.repo = repo or CurationRepository()

    def list_jobs(self, page: int, limit: int) -> tuple[list[dict], int]:
        """검수 큐(페이지)를 조회하고 표시용 타입으로 정규화한다."""
        offset = (page - 1) * limit
        rows, total = self.repo.list_jobs(limit, offset)
        jobs = [
            {
                "job_id": int(r["job_id"]),
                "invoice_id": r["invoice_id"],
                "curation_reviewed": bool(r["curation_reviewed"]),
                "pair_count": int(r["pair_count"]),
                "unreviewed_count": int(r["unreviewed_count"] or 0),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        return jobs, total

    def get_detail(self, job_id: int) -> dict:
        """잡 상세(행별 top5 조인 포함)를 조회한다. 없으면 404."""
        detail = self.repo.find_job_detail(job_id)
        if detail is None:
            not_found("OCR 잡을 찾을 수 없습니다.")
        job = detail["job"]
        result = job.get("result_json") or {}
        top5_by_row = {
            r.get("row_index"): (r.get("item_top5") or []) for r in result.get("rows", [])
        }
        pairs = [
            {
                "id": int(p["id"]),
                "crop_ref": p["crop_ref"],
                "row_index": int(p["row_index"]),
                "draft_label": p["draft_label"],
                "final_label": p["final_label"],
                "canonical_label": p["canonical_label"],
                "supply": p["supply"],
                "status": p["status"],
                "reviewed_at": p["reviewed_at"],
                "top5": top5_by_row.get(int(p["row_index"]), []),
            }
            for p in detail["pairs"]
        ]
        return {
            "job_id": int(job["id"]),
            "invoice_id": job["invoice_id"],
            "curation_reviewed": bool(job["curation_reviewed"]),
            "warp_ok": bool(result.get("warp_ok", False)),
            "created_at": job["created_at"],
            "pairs": pairs,
        }

    def patch_pair(self, pair_id: int, fields: dict) -> dict:
        """학습쌍을 부분 갱신하고 갱신된 쌍을 반환한다. 없으면 404."""
        if self.repo.find_pair(pair_id) is None:
            not_found("학습쌍을 찾을 수 없습니다.")
        self.repo.update_pair(pair_id, fields)
        updated = self.repo.find_pair(pair_id)
        return {
            "id": int(updated["id"]),
            "crop_ref": updated["crop_ref"],
            "job_id": int(updated["job_id"]),
            "row_index": int(updated["row_index"]),
            "draft_label": updated["draft_label"],
            "final_label": updated["final_label"],
            "canonical_label": updated["canonical_label"],
            "supply": updated["supply"],
            "status": updated["status"],
            "reviewed_at": updated["reviewed_at"],
        }

    def mark_reviewed(self, job_id: int) -> dict:
        """잡을 검수완료로 표시한다. 없으면 404. 멱등."""
        if not self.repo.job_exists(job_id):
            not_found("OCR 잡을 찾을 수 없습니다.")
        self.repo.mark_reviewed(job_id)
        return {"job_id": job_id, "curation_reviewed": True}

    def _data_dir(self) -> Path:
        raw = os.environ.get("SJMJ_DATA_DIR")
        if not raw:
            # 오설정 가드: SJMJ_DATA_DIR 누락 시 명확 실패(운영 전용 — 테스트는 항상 설정).
            raise RuntimeError("SJMJ_DATA_DIR 미설정 — 이미지 경로 조립 불가")
        return Path(raw)

    def original_image(self, job_id: int) -> str:
        """원본 업로드 이미지 절대경로를 반환한다. 없으면 404."""
        if not self.repo.job_exists(job_id):
            not_found("OCR 잡을 찾을 수 없습니다.")
        path = self.repo.get_image_path(job_id)
        if not path or not Path(path).is_file():
            not_found("원본 이미지가 없습니다.")
        return path

    def warped_image(self, job_id: int) -> str:
        """워프된 전표 이미지 절대경로를 반환한다. 없으면 404."""
        if not self.repo.job_exists(job_id):
            not_found("OCR 잡을 찾을 수 없습니다.")
        path = self._data_dir() / "ocr_crops" / f"job-{job_id}" / "warped.png"
        if not path.is_file():
            not_found("워프 이미지가 없습니다.")
        return str(path)

    def crop_image(self, job_id: int, row: int) -> str:
        """행 crop 이미지 절대경로를 반환한다. 없으면 404."""
        if not self.repo.job_exists(job_id):
            not_found("OCR 잡을 찾을 수 없습니다.")
        path = self._data_dir() / "ocr_crops" / f"job-{job_id}" / f"row-{row}.png"
        if not path.is_file():
            not_found("crop 이미지가 없습니다.")
        return str(path)
