"""
Tests de POST /api/v1/auth/login: credenciales válidas/inválidas, y bloqueo
por intentos fallidos (issue #7).
"""
from app.core.redis_client import MAX_INTENTOS_FALLIDOS


def test_login_exitoso_devuelve_access_y_refresh(client, crear_admin):
    resp = client.post("/api/v1/auth/login", json=crear_admin)

    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_login_con_password_incorrecta_devuelve_401(client, crear_admin):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": crear_admin["email"], "password": "password-incorrecta"},
    )

    assert resp.status_code == 401
    assert resp.json()["mensaje"] == "Email o contraseña incorrectos."


def test_login_con_email_inexistente_devuelve_401(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "no-existe@test.com", "password": "cualquier-cosa"},
    )

    assert resp.status_code == 401


def test_login_bloquea_tras_maximo_de_intentos_fallidos(client, crear_admin):
    email = crear_admin["email"]

    for _ in range(MAX_INTENTOS_FALLIDOS - 1):
        resp = client.post("/api/v1/auth/login", json={"email": email, "password": "mal"})
        assert resp.status_code == 401

    # El intento número MAX_INTENTOS_FALLIDOS activa el bloqueo
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": "mal"})
    assert resp.status_code == 429

    # Incluso con la contraseña CORRECTA, ahora el login está bloqueado
    resp = client.post("/api/v1/auth/login", json=crear_admin)
    assert resp.status_code == 429
    assert "segundos" in resp.json()["mensaje"]


def test_login_exitoso_reinicia_el_contador_de_intentos_fallidos(client, crear_admin):
    email = crear_admin["email"]

    # Un par de intentos fallidos, sin llegar al máximo
    client.post("/api/v1/auth/login", json={"email": email, "password": "mal"})
    client.post("/api/v1/auth/login", json={"email": email, "password": "mal"})

    # Login correcto: reinicia el contador
    resp = client.post("/api/v1/auth/login", json=crear_admin)
    assert resp.status_code == 200

    # Debería poder seguir fallando MAX_INTENTOS_FALLIDOS - 1 veces más sin bloquearse
    for _ in range(MAX_INTENTOS_FALLIDOS - 1):
        resp = client.post("/api/v1/auth/login", json={"email": email, "password": "mal"})
        assert resp.status_code == 401  # no 429: el contador se reinició


def test_login_usuario_inactivo_devuelve_401(client, db_session, crear_admin):
    from app.models.usuario import Usuario

    usuario = db_session.query(Usuario).filter(Usuario.email == crear_admin["email"]).first()
    usuario.activo = False
    db_session.commit()

    resp = client.post("/api/v1/auth/login", json=crear_admin)

    assert resp.status_code == 401
