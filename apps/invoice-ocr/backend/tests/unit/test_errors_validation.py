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


class _Item(BaseModel):
    label: str


class _ArrayBody(BaseModel):
    rows: list[_Item]


def _array_app() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/arr")
    def arr(body: _ArrayBody):  # noqa: D103
        return {"ok": True}

    return TestClient(app)


def test_array_validation_keys_preserve_index_path():
    # 두 행이 같은 leaf(label)에서 실패 → loc[-1]만 쓰면 키 충돌로 한 건만 남는다.
    res = _array_app().post("/arr", json={"rows": [{}, {}]})
    assert res.status_code == 400
    details = res.json()["error"]["details"]
    # 경로를 보존하므로 두 행의 에러가 별도 키로 분리되어 둘 다 노출된다.
    assert "rows.0.label" in details
    assert "rows.1.label" in details
