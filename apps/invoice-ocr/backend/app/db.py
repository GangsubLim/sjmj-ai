"""DB 연결 — SQLAlchemy Engine + connection/transaction 컨텍스트.

PHP Database 싱글톤 동형. repository는 `with connection() as conn:`으로 현재
바인딩된 conn을 재사용하고, service는 `with transaction():`으로 tx를 열어
내부 repo 호출이 같은 conn을 쓰게 한다. 테스트는 set_test_engine + _conn_var
바인딩으로 단일 tx 격리(롤백)를 만든다.
"""

from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import Engine, create_engine

from .config import get_settings

_engine: Engine | None = None
_conn_var: ContextVar = ContextVar("sjmj_conn", default=None)


def get_engine() -> Engine:
    """모듈 전역 SQLAlchemy Engine을 반환한다(없으면 설정으로 생성)."""
    global _engine
    if _engine is None:
        s = get_settings()
        url = (
            f"mysql+pymysql://{s.db_user}:{s.db_pass}@{s.db_host}:{s.db_port}"
            f"/{s.db_name}?charset=utf8mb4"
        )
        _engine = create_engine(url, pool_pre_ping=True, future=True)
    return _engine


def set_test_engine(engine: Engine) -> None:
    """전역 Engine을 테스트용 엔진으로 교체한다."""
    global _engine
    _engine = engine


def reset_engine() -> None:
    """전역 Engine을 초기화한다(다음 호출 시 재생성)."""
    global _engine
    _engine = None


@contextmanager
def connection():
    """현재 바인딩된 conn이 있으면 재사용, 없으면 엔진에서 새 트랜잭션을 연다.

    바인딩이 없을 때 `engine.begin()`을 쓰는 이유: SQLAlchemy 2.0의 Connection은
    자동 커밋이 아니므로(블록 종료 시 롤백), PHP PDO(문장별 autocommit)와 동등하게
    하려면 standalone repo 호출이 블록 종료 시 커밋되도록 begin()으로 감싼다.
    바인딩이 있으면(= service.transaction() 안) 그 단일 tx에 합류한다.
    """
    existing = _conn_var.get()
    if existing is not None:
        yield existing
        return
    with get_engine().begin() as conn:
        yield conn


@contextmanager
def transaction():
    """트랜잭션 시작 + conn 바인딩(내부 repo가 같은 conn 재사용). 이미 tx면 중첩 재사용."""
    existing = _conn_var.get()
    if existing is not None:
        yield existing
        return
    with get_engine().begin() as conn:
        token = _conn_var.set(conn)
        try:
            yield conn
        finally:
            _conn_var.reset(token)
