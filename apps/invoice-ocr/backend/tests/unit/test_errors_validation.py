"""RequestValidationError → 400 VALIDATION_ERROR envelope 변환 핸들러 단위 검증."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, model_validator

from app.core.errors import register_error_handlers


class _Body(BaseModel):
    name: str


def _app() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/echo")
    def echo(body: _Body):  # noqa: D103
        return {"ok": body.name}

    return TestClient(app)


def test_pydantic_validation_failure_becomes_400_envelope():
    res = _app().post("/echo", json={})  # name 누락 → 기본 422
    assert res.status_code == 400
    payload = res.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert "name" in payload["error"]["details"]


def test_validation_details_key_is_field_name():
    res = _app().post("/echo", json={"name": 123})  # 타입 불일치
    body = res.json()
    assert body["error"]["details"]["name"]  # 비어있지 않은 메시지


class _ModelBody(BaseModel):
    a: str | None = None

    @model_validator(mode="after")
    def _need_a(self):
        if self.a is None:
            raise ValueError("a가 필요합니다.")
        return self


def _model_app() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/m")
    def m(body: _ModelBody):  # noqa: D103
        return {"ok": True}

    return TestClient(app)


def test_model_level_validation_keys_under_body():
    res = _model_app().post("/m", json={})  # model_validator 실패 → loc=("body",)
    assert res.status_code == 400
    details = res.json()["error"]["details"]
    assert "body" in details  # whole-object 에러는 "body" 키로 고정
    assert details["body"]  # 비어있지 않은 메시지
