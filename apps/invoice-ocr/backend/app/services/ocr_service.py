"""OCR 잡 업로드·조회·확정. confirm은 행잠금 claim으로 중복 invoice 생성을 막는다."""

import logging
import re
import uuid
from pathlib import Path

from app import db
from app.config import crop_dir, data_root
from app.core.errors import bad_request, conflict, not_found
from app.repositories.companies_repository import CompanyRepository
from app.repositories.curation_repository import CurationRepository
from app.repositories.items_repository import ItemRepository
from app.repositories.ocr_repository import OcrRepository
from app.services.invoice_service import InvoiceService
from app.services.ocr_correction import build_correction, build_training_pairs
from app.services.ocr_observation import derive_observation_status

logger = logging.getLogger(__name__)


def _upload_root() -> Path:
    p = data_root() / "ocr_uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _crops_observable() -> bool:
    """데이터 루트가 살아 있어 crop 산출물을 볼 수 있는지 페이지당 1회 확인한다.

    data_root()는 SJMJ_DATA_DIR 미설정·부재에서 RuntimeError를 던진다. 관측 목록은
    읽기 전용 진단 화면이므로(ADR 0009) 여기서 500으로 죽으면 안 된다 — 하필 그 오설정을
    진단하려고 여는 화면이고, DB만으로 판정되는 pending/running/failed 행까지 함께 사라진다.
    삼키지 않고 경고 로그를 남긴다(운영자는 warp 배지가 아니라 로그로 원인을 본다).

    Returns:
        데이터 루트가 살아 있으면 True. False면 warped.png를 "관측 불가"로 다룬다.
    """
    try:
        data_root()
    except RuntimeError as exc:
        logger.warning("데이터 루트 접근 불가 — warped.png 관측을 건너뛴다: %s", exc)
        return False
    return True


_ALLOWED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
# 저장 경로에 이어붙는 것은 suffix뿐(stem은 폐기)이며 화이트리스트가 주 방어선.
# 제어문자 검사는 이슈 #21 AC가 요구한 심층 방어(비용 0)로,
# salespeople_service._CONTROL_CHAR와 같은 범위다(수정 시 함께 갱신).
_CONTROL_CHAR = re.compile(r"[\x00-\x1F\x7F]")

# invoice_items에 대응 컬럼이 없는 OCR/UI 전용 필드 — invoice 생성 payload에서 걷어낸다.
# (repository가 명시 컬럼만 읽어 SQL에는 닿지 않지만, 경계를 코드로 드러내 둔다.)
_NON_INVOICE_ITEM_KEYS = frozenset({"crop_ref", "label_source"})

# status == "failed"인데 실패 사유가 없을 때(worker가 error를 못 남긴 경우)의 폴백 문구.
# get_job(단건 조회)과 _observe(목록 관측)가 동일 문구를 공유한다.
_DEFAULT_FAILURE_MESSAGE = "추론 실패"


def _validated_suffix(filename: str) -> str:
    """업로드 파일명을 검증하고 소문자로 정규화한 확장자를 반환한다.

    Args:
        filename: 클라이언트가 보낸 원본 파일명(신뢰할 수 없는 입력).

    Returns:
        `.jpg` / `.jpeg` / `.png` 중 하나(소문자).

    Raises:
        AppError: 파일명에 제어문자가 있거나 허용되지 않은 확장자일 때 400 VALIDATION_ERROR.
    """
    name = filename or ""
    if _CONTROL_CHAR.search(name):
        bad_request(
            "파일명에 허용되지 않는 문자가 포함되어 있습니다.",
            {"photo": "파일명에 제어문자를 사용할 수 없습니다."},
        )
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        bad_request(
            "jpg/jpeg/png 형식만 업로드할 수 있습니다.",
            {"photo": "jpg/jpeg/png 확장자만 업로드할 수 있습니다."},
        )
    return suffix


class OcrService:
    """OCR 잡 업로드·조회·확정을 담당하는 서비스."""

    def __init__(self, repo=None, invoice_service=None, *, transaction=None, curation_repo=None):
        """저장소·invoice_service·트랜잭션 seam·큐레이션 저장소를 주입받아 초기화한다."""
        self.repo = repo or OcrRepository()
        self.invoice_service = invoice_service or InvoiceService(
            company_repo=CompanyRepository(), item_repo=ItemRepository()
        )
        self._transaction = transaction or db.transaction
        self.curation_repo = curation_repo or CurationRepository()

    def create_job(self, photo_bytes: bytes, filename: str) -> dict:
        """업로드 이미지를 저장하고 OCR 잡을 생성해 job_id와 상태를 반환한다.

        Raises:
            AppError: 파일명이 허용 확장자(.jpg/.jpeg/.png)가 아니거나 제어문자를 포함할 때 400.
        """
        suffix = _validated_suffix(filename)
        dest = _upload_root() / f"{uuid.uuid4().hex}{suffix}"
        dest.write_bytes(photo_bytes)
        job_id = self.repo.insert_job(str(dest))
        return {"job_id": job_id, "status": "pending"}

    def get_job(self, job_id: int) -> dict | None:
        """OCR 잡 상태와 추론 결과(또는 실패 사유)를 조회한다(없으면 None)."""
        job = self.repo.find_job(job_id)
        if job is None:
            return None
        out = {"id": job["id"], "status": job["status"]}
        result = job.get("result_json")
        if job["status"] == "failed":
            out["error"] = (result or {}).get("error", _DEFAULT_FAILURE_MESSAGE)
        elif result is not None:
            out["result"] = result
        return out

    def confirm(self, job_id: int, payload: dict) -> dict:
        """OCR 잡을 행잠금 claim으로 확정해 거래명세서를 생성하고 교정 이력을 남긴다.

        중복 invoice 생성을 막기 위해 claim_job/link_invoice로 직렬화한다.
        """
        with self._transaction():
            job = self.repo.claim_job(job_id)
            if job is None:
                not_found("OCR 잡을 찾을 수 없습니다.")
            if job["invoice_id"] is not None:
                conflict("이미 확정된 잡입니다.")
            if job["status"] != "done" or job.get("result_json") is None:
                conflict("아직 확정할 수 없는 잡입니다(추론 미완료).")

            # OCR/UI 전용 필드를 제거한 뒤 invoice 생성(_NON_INVOICE_ITEM_KEYS 주석 참조).
            # build_correction은 아래에서 원본 payload를 받는다 — label_source가 살아 있어야 한다.
            invoice_payload = {
                **payload,
                "items": [
                    {k: v for k, v in item.items() if k not in _NON_INVOICE_ITEM_KEYS}
                    for item in payload.get("items", [])
                ],
            }
            invoice = self.invoice_service.create(invoice_payload)
            invoice_id = invoice["id"]

            if self.repo.link_invoice(job_id, invoice_id) == 0:
                conflict("이미 확정된 잡입니다.")

            correction = build_correction(job["result_json"] or {}, payload.get("items", []))
            self.repo.insert_correction(job_id, invoice_id, correction)
            pairs = build_training_pairs(job_id, invoice_id, correction)
            self.curation_repo.insert_training_pairs(pairs)

        return {"invoice_id": invoice_id}

    def crop_image(self, job_id: int, row: int) -> str:
        """행 crop 이미지 절대경로를 반환한다. 잡·파일이 없으면 404.

        crop PNG는 워커의 추론 시점(infer_job)에 저장되므로 confirm 이전에도 존재한다.
        경로는 서버가 (job_id, row) 정수로만 조립한다 — crop_ref 문자열을 신뢰하지 않는다.
        존재 확인은 result_json을 파싱하는 find_job이 아니라 경량 job_exists를 쓴다 —
        등록 UI가 행마다 이 경로를 호출하므로(썸네일 N개), 잡당 1회면 될 파싱 비용이
        행 수만큼 반복되는 것을 막는다(부수로 result_json이 깨진 잡도 500 대신 정상 404).
        """
        if not self.repo.job_exists(job_id):
            not_found("OCR 잡을 찾을 수 없습니다.")
        path = crop_dir(job_id) / f"row-{row}.png"
        if not path.is_file():
            not_found("crop 이미지가 없습니다.")
        return str(path)

    def list_unconfirmed(self, page: int, limit: int) -> tuple[list[dict], int]:
        """미확정 잡을 페이지 조회하고 관측 상태 배지를 붙여 반환한다(읽기 전용).

        Args:
            page: 1-base 페이지 번호(라우터에서 clamp된 값).
            limit: 페이지 크기(라우터에서 1..100으로 clamp된 값).

        Returns:
            (관측용 잡 dict 리스트, 전체 건수).
        """
        offset = (page - 1) * limit
        rows, total = self.repo.list_unconfirmed(limit, offset)
        crops_observable = _crops_observable()
        return [self._observe(r, crops_observable=crops_observable) for r in rows], total

    def _observe(self, row: dict, *, crops_observable: bool) -> dict:
        """DB 행을 관측용 표시 타입으로 정규화한다(warped.png 존재 여부만 FS에서 채운다).

        row_count는 rows가 배열일 때만 정수다 — 추론 미완·계약 위반에서는 None으로 내보내
        화면이 "0행 검출"과 "아직 모름"을 갈라 그릴 수 있게 한다.

        Args:
            row: repo.list_unconfirmed가 올린 원값 행.
            crops_observable: 데이터 루트가 살아 있는지(_crops_observable). False면 강등이
                아니라 NO_WARP로 닫힌다 — 그 배지는 이미 "볼 워프 산출이 없다"까지만 말한다
                (ocr_observation 모듈 docstring: 저장 실패·사후 유실도 같은 관측으로 본다).
        """
        job_id = int(row["job_id"])
        rows_type = row["rows_type"]
        raw_count = row["row_count"]
        row_count = int(raw_count) if rows_type == "ARRAY" and raw_count is not None else None
        error = row["error"]
        # 페이지당 최대 limit(<=100)회 stat. 게이트 지표 없이 강등/워프 없음을 가르는 유일한 신호다.
        has_warped = crops_observable and (crop_dir(job_id) / "warped.png").is_file()
        if row["status"] == "failed" and error is None:
            error = _DEFAULT_FAILURE_MESSAGE
        return {
            "job_id": job_id,
            "observation_status": derive_observation_status(
                status=row["status"],
                warp_ok=row["warp_ok"],
                rows_type=rows_type,
                row_count=row_count,
                has_warped=has_warped,
            ),
            "row_count": row_count,
            "error": str(error) if error is not None else None,
            "created_at": row["created_at"],
        }
