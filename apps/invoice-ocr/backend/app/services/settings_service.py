"""SettingsService.

uploadStamp의 파일 저장 흐름(issuer 확인 → 크기·MIME
검증 → 고정 파일명 덮어쓰기). 검증 실패는 app.core.errors.bad_request(400).
"""

from pathlib import Path

from app.core.errors import bad_request
from app.repositories.settings_repository import SettingsRepository

_STAMP_MAX_SIZE = 500 * 1024  # 500KB
_STAMP_ALLOWED_TYPES = {"image/png", "image/jpeg"}
_STAMP_UPLOAD_DIR = Path(__file__).resolve().parents[1].parent / "uploads" / "stamps"

_ALLOWED_SETTING_KEYS = (
    "default_vat_rate",
    "default_document_title",
    "default_unit",
    "pdf_filename_pattern",
)


class SettingsService:
    """발행자 정보·도장 이미지·앱 설정을 다루는 서비스."""

    def __init__(self, repo=None):
        """저장소 의존을 주입한다(없으면 기본 SettingsRepository)."""
        self.repo = repo or SettingsRepository()

    def get_issuer(self) -> dict | None:
        """발행자 정보를 단건 조회한다."""
        return self.repo.find_issuer()

    def update_issuer(self, data: dict) -> dict | None:
        """발행자 정보를 upsert하고 갱신된 결과를 반환한다.

        phone/fax 조합으로 tel_fax 표시값을 NULL-safe하게 생성한다.
        """
        # tel_fax 자동 생성 (NULL-safe)
        phone = data.get("phone")
        fax = data.get("fax")
        if phone and fax:
            data = {**data, "tel_fax": f"{phone}/{fax}"}
        elif phone:
            data = {**data, "tel_fax": phone}
        elif fax:
            data = {**data, "tel_fax": fax}
        else:
            data = {**data, "tel_fax": None}

        self.repo.upsert_issuer(data)
        return self.repo.find_issuer()

    def upload_stamp(self, content: bytes, content_type: str | None) -> dict | None:
        """도장 이미지를 검증·저장하고 URL을 반환한다.

        발행자가 없으면 None. 크기·MIME 검증 실패는 bad_request(400)로 던진다.
        """
        issuer = self.repo.find_issuer()
        if not issuer:
            return None

        if len(content) > _STAMP_MAX_SIZE:
            bad_request("파일 크기는 500KB 이하여야 합니다.")
        if content_type not in _STAMP_ALLOWED_TYPES:
            bad_request("PNG 또는 JPG 파일만 업로드 가능합니다.")

        ext = "png" if content_type == "image/png" else "jpg"
        filename = f"stamp_{issuer['id']}.{ext}"
        _STAMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        (_STAMP_UPLOAD_DIR / filename).write_bytes(content)

        url = f"/uploads/stamps/{filename}"
        self.repo.update_stamp_url(issuer["id"], url)
        return {"stamp_image_url": url}

    def get_app_settings(self) -> dict:
        """앱 설정 전체를 조회한다."""
        return self.repo.find_all_settings()

    def update_app_settings(self, settings: dict) -> dict:
        """허용된 키만 골라 앱 설정을 갱신하고 전체 설정을 반환한다."""
        for key, value in settings.items():
            if key in _ALLOWED_SETTING_KEYS:
                self.repo.update_setting(key, value)
        return self.repo.find_all_settings()
