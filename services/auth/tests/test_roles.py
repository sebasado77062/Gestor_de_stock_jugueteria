"""
Tests de control de acceso por rol (issue #7: roles admin/empleado).
"""


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_me_requiere_autenticacion(client):
    resp = client.get("/api/v1/auth/me")

    assert resp.status_code == 401


def test_me_devuelve_los_datos_del_usuario_autenticado(client, admin_token, crear_admin):
    resp = client.get("/api/v1/auth/me", headers=_auth_header(admin_token["access_token"]))

    assert resp.status_code == 200
    assert resp.json()["email"] == crear_admin["email"]
    assert resp.json()["rol"] == "admin"


def test_admin_puede_crear_usuarios(client, admin_token):
    resp = client.post(
        "/api/v1/auth/usuarios",
        json={"email": "nuevo@test.com", "password": "password123", "nombre": "Nuevo", "rol": "empleado"},
        headers=_auth_header(admin_token["access_token"]),
    )

    assert resp.status_code == 201
    assert resp.json()["rol"] == "empleado"


def test_empleado_no_puede_crear_usuarios(client, crear_empleado):
    login = client.post("/api/v1/auth/login", json=crear_empleado).json()

    resp = client.post(
        "/api/v1/auth/usuarios",
        json={"email": "otro@test.com", "password": "password123", "nombre": "Otro", "rol": "empleado"},
        headers=_auth_header(login["access_token"]),
    )

    assert resp.status_code == 403


def test_empleado_no_puede_listar_usuarios(client, crear_empleado):
    login = client.post("/api/v1/auth/login", json=crear_empleado).json()

    resp = client.get("/api/v1/auth/usuarios", headers=_auth_header(login["access_token"]))

    assert resp.status_code == 403


def test_admin_puede_listar_usuarios(client, admin_token, crear_empleado):
    resp = client.get("/api/v1/auth/usuarios", headers=_auth_header(admin_token["access_token"]))

    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_crear_usuario_con_email_duplicado_devuelve_409(client, admin_token, crear_admin):
    resp = client.post(
        "/api/v1/auth/usuarios",
        json={"email": crear_admin["email"], "password": "password123", "nombre": "Duplicado", "rol": "empleado"},
        headers=_auth_header(admin_token["access_token"]),
    )

    assert resp.status_code == 409


def test_empleado_puede_ver_su_propio_perfil(client, crear_empleado):
    """/me acepta cualquiera de los dos roles, a diferencia de /usuarios."""
    login = client.post("/api/v1/auth/login", json=crear_empleado).json()

    resp = client.get("/api/v1/auth/me", headers=_auth_header(login["access_token"]))

    assert resp.status_code == 200
    assert resp.json()["rol"] == "empleado"


def test_token_con_firma_invalida_es_rechazado(client):
    resp = client.get("/api/v1/auth/me", headers=_auth_header("token.invalido.falso"))

    assert resp.status_code == 401
