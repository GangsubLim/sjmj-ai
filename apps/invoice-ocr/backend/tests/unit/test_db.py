from sqlalchemy import create_engine, text

from app import db as dbmod


def test_connection_reuses_bound_conn():
    engine = create_engine("sqlite://")
    dbmod.set_test_engine(engine)
    try:
        conn = engine.connect()
        token = dbmod._conn_var.set(conn)
        try:
            with dbmod.connection() as c1, dbmod.connection() as c2:
                assert c1 is conn and c2 is conn  # 바인딩 conn 재사용
        finally:
            dbmod._conn_var.reset(token)
            conn.close()
    finally:
        dbmod.reset_engine()


def test_connection_opens_new_when_unbound():
    engine = create_engine("sqlite://")
    dbmod.set_test_engine(engine)
    try:
        with dbmod.connection() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        dbmod.reset_engine()


def test_transaction_binds_connection():
    engine = create_engine("sqlite://")
    dbmod.set_test_engine(engine)
    try:
        with dbmod.transaction() as conn:
            assert dbmod._conn_var.get() is conn
            conn.execute(text("SELECT 1"))
        assert dbmod._conn_var.get() is None  # 종료 후 해제
    finally:
        dbmod.reset_engine()
