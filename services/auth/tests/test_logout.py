"""
Tests de POST /api/v1/auth/logout: revocación de access token (blacklist
con TTL) y del refresh token asociado.
"""


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_logout_devuelve_204(client, admin_token):
    resp = client.post("/api/v1/auth/logout", headers=_auth_header(admin_token["access_token"]))

    assert resp.status_code == 204


def test_logout_sin_token_devuelve_401(client):
    resp = client.post("/api/v1/auth/logout")

    assert resp.status_code == 401


def test_access_token_revocado_tras_logout_no_sirve_mas(client, admin_token):
    access = admin_token["access_token"]

    client.post("/api/v1/auth/logout", headers=_auth_header(access))

    resp = client.get("/api/v1/auth/me", headers=_auth_header(access))

    assert resp.status_code == 401
    assert "revocado" in resp.json()["mensaje"]


def test_logout_con_refresh_token_tambien_lo_invalida(client, admin_token):
    access = admin_token["access_token"]
    refresh = admin_token["refresh_token"]

    resp_logout = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh},
        headers=_auth_header(access),
    )
    assert resp_logout.status_code == 204

    resp_refresh = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert resp_refresh.status_code == 401


def test_logout_sin_enviar_refresh_token_no_lo_invalida(client, admin_token):
    """
    Si el cliente hace logout mandando solo el access token (sin el body
    con refresh_token), el refresh token sigue siendo válido: el logout
    solo revoca lo que explícitamente se le pide revocar.
    """
    access = admin_token["access_token"]
    refresh = admin_token["refresh_token"]

    client.post("/api/v1/auth/logout", headers=_auth_header(access))

    resp_refresh = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert resp_refresh.status_code == 200


def test_access_token_de_otra_sesion_sigue_funcionando_tras_logout_de_una(client, crear_admin):
    """
    Loguearse dos veces genera dos access tokens (dos jti distintos). Cerrar
    sesión con uno no debe afectar al otro.
    """
    login1 = client.post("/api/v1/auth/login", json=crear_admin).json()
    login2 = client.post("/api/v1/auth/login", json=crear_admin).json()

    client.post("/api/v1/auth/logout", headers=_auth_header(login1["access_token"]))

    resp = client.get("/api/v1/auth/me", headers=_auth_header(login2["access_token"]))
    assert resp.status_code == 200
