"""settings 라우터 — PHP controllers/SettingsController.php 동형.

issuer 정보(단일)·도장 업로드(멀티파트)·앱 설정(키-값 맵)을 다룬다. 엔드포인트는
sync def. 도장 업로드는 UploadFile(File(None))로 파일 부재(400)를 명시 처리한다.
"""

from fastapi import APIRouter, Body, File, UploadFile

from app.core import envelope
from app.core.errors import bad_request, not_found
from app.core.validators import Validator
from app.services.settings_service import SettingsService

router = APIRouter()


def _service() -> SettingsService:
    return SettingsService()


@router.get("/settings/issuer")
def get_issuer():
    issuer = _service().get_issuer()
    if not issuer:
        not_found("발급자 정보가 없습니다.")
    return envelope.single(issuer)


@router.put("/settings/issuer")
def update_issuer(data: dict = Body(...)):
    Validator().required(
        data, ["company_name", "representative", "business_number", "address"]
    ).business_number(data, "business_number").validate_or_fail()
    issuer = _service().update_issuer(data)
    return envelope.single(issuer)


@router.post("/settings/issuer/stamp")
def upload_stamp(image: UploadFile | None = File(None)):
    if image is None:
        bad_request("이미지 파일이 필요합니다.")
    content = image.file.read()
    result = _service().upload_stamp(content, image.content_type)
    if not result:
        not_found("발급자 정보가 없습니다. 먼저 발급자 정보를 등록해주세요.")
    return envelope.single(result)


@router.get("/settings/app")
def get_app_settings():
    return envelope.single(_service().get_app_settings())


@router.put("/settings/app")
def update_app_settings(data: dict = Body(...)):
    if not data:
        bad_request("수정할 설정값이 필요합니다.")
    return envelope.single(_service().update_app_settings(data))
