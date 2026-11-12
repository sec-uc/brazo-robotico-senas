"""Servicio auth: emisión e introspección de tokens (QA-09)."""
import jwt
import pytest
from fastapi.testclient import TestClient

from rsp_testkit import SECRET, make_token


@pytest.fixture
def client(monkeypatch, auth_env):
    monkeypatch.setenv("AUTH_USERS", "ana:clave1:operator,admin:clave2:admin|operator,roto:sin-rol")
    monkeypatch.setenv("AUTH_TOKEN_TTL_S", "120")
    import app.main as m
    return TestClient(m.app), m


def test_issues_jwt_with_roles_and_ttl(client):
    c, m = client
    r = c.post("/token", json={"username": "admin", "password": "clave2"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 120
    claims = jwt.decode(body["access_token"], SECRET, algorithms=["HS256"])
    assert claims["sub"] == "admin" and claims["roles"] == ["admin", "operator"]
    assert claims["exp"] - claims["iat"] == 120


@pytest.mark.parametrize("user,pwd", [("ana", "mal"), ("nadie", "clave1"), ("roto", "sin-rol")])
def test_rejects_bad_credentials_and_malformed_users(client, user, pwd):
    c, _ = client
    assert c.post("/token", json={"username": user, "password": pwd}).status_code == 401


def test_missing_fields_is_422(client):
    c, _ = client
    assert c.post("/token", json={"username": "ana"}).status_code == 422


def test_introspect_active_and_inactive(client):
    c, _ = client
    ok = c.post("/introspect", json={"token": make_token("ana")}).json()
    assert ok["active"] is True and ok["sub"] == "ana"
    for bad in ("basura", make_token(ttl=-5), make_token(secret="z" * 40)):
        res = c.post("/introspect", json={"token": bad}).json()
        assert res["active"] is False and "error" in res


def test_default_demo_users_exist(monkeypatch, auth_env):
    monkeypatch.delenv("AUTH_USERS", raising=False)
    import app.main as m
    users = m._users()
    assert users["operator"]["roles"] == ["operator"] and "admin" in users["admin"]["roles"]


def test_auth_does_not_need_the_bus(client):
    c, _ = client
    assert c.get("/health").json()["status"] == "ok"


def test_users_live_in_the_service_database_with_hashed_passwords(monkeypatch, auth_env, tmp_path):
    """ADR-014: usuarios en la base propia, sembrados una vez desde AUTH_USERS; la contraseña no se guarda."""
    import importlib
    monkeypatch.setenv("AUTH_USERS", "ana:clave1:operator")
    monkeypatch.setenv("AUTH_DATABASE_URL", str(tmp_path / "auth.db"))
    import app.main as m
    importlib.reload(m)
    try:
        with TestClient(m.app) as c:  # el arranque crea y siembra la tabla
            assert c.post("/token", json={"username": "ana", "password": "clave1"}).status_code == 200
            assert c.post("/token", json={"username": "ana", "password": "otra"}).status_code == 401
            assert c.post("/token", json={"username": "nadie", "password": "clave1"}).status_code == 401
        with m.db.conn() as conn:
            row = conn.execute("SELECT * FROM users").fetchone()
        assert row["username"] == "ana" and row["roles"] == "operator" and "clave1" not in str(dict(row))
        # con la tabla ya poblada, cambiar AUTH_USERS no cambia los usuarios: manda la base
        monkeypatch.setenv("AUTH_USERS", "otro:x:admin")
        m.init_db()
        assert m.authenticate("otro", "x") is None and m.authenticate("ana", "clave1") == ["operator"]
    finally:
        monkeypatch.delenv("AUTH_DATABASE_URL")
        importlib.reload(m)
