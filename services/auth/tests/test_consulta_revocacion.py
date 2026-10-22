"""
Tests de GET /api/v1/auth/tokens/{jti}/revocado: endpoint pensado solo para el
modo local sin Docker, donde productos y movimientos no comparten Redis con
auth y le preguntan a auth si un token fue revocado por logout.
"""
from jose import jwt

from app.core.security import ALGORITHM, SECRET_KEY


def _jti_de(token: str) -> str:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["jti"]


def test_esta_deshabilitado_por_defecto(client, admin_token, monkeypatch):
    monkeypatch.delenv("HABILITAR_CONSULTA_REVOCACION", raising=False)

    resp = client.get(f"/api/v1/auth/tokens/{_jti_de(admin_token['access_token'])}/revocado")

    assert resp.status_code == 404


def test_habilitado_un_token_vigente_no_figura_revocado(client, admin_token, monkeypatch):
    monkeypatch.setenv("HABILITAR_CONSULTA_REVOCACION", "true")
    jti = _jti_de(admin_token["access_token"])

    resp = client.get(f"/api/v1/auth/tokens/{jti}/revocado")

    assert resp.status_code == 200
    assert resp.json() == {"jti": jti, "revocado": False}


def test_habilitado_tras_el_logout_el_token_figura_revocado(client, admin_token, monkeypatch):
    monkeypatch.setenv("HABILITAR_CONSULTA_REVOCACION", "true")
    access = admin_token["access_token"]
    jti = _jti_de(access)

    client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {access}"})
    resp = client.get(f"/api/v1/auth/tokens/{jti}/revocado")

    assert resp.status_code == 200
    assert resp.json()["revocado"] is True


def test_un_jti_desconocido_no_figura_revocado(client, monkeypatch):
    monkeypatch.setenv("HABILITAR_CONSULTA_REVOCACION", "true")

    resp = client.get("/api/v1/auth/tokens/jti-que-no-existe/revocado")

    assert resp.json()["revocado"] is False
