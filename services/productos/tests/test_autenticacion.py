"""
Tests de la integración de JWT en services/productos (issue #7).

A diferencia del resto de la suite (que usa la fixture `client`, con la
autenticación simulada vía dependency_overrides), estos tests usan
`client_sin_auth`: ejercitan el mecanismo REAL de validación de JWT
(app/core/jwt_auth.py) con tokens generados a mano con la misma
AUTH_SECRET_KEY que usaría el servicio auth real.
"""
import time

import pytest
from jose import jwt

from app.core.jwt_auth import SECRET_KEY, ALGORITHM


def _generar_token(rol="admin", tipo="access", segundos_para_expirar=900, jti="jti-test"):
    ahora = int(time.time())
    payload = {
        "sub": "1",
        "email": "usuario@test.com",
        "rol": rol,
        "tipo": tipo,
        "jti": jti,
        "iat": ahora,
        "exp": ahora + segundos_para_expirar,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_listar_productos_sin_token_devuelve_401(client_sin_auth):
    resp = client_sin_auth.get("/api/v1/productos")

    assert resp.status_code == 401
    assert "autenticación" in resp.json()["mensaje"]


def test_listar_productos_con_token_valido_devuelve_200(client_sin_auth):
    token = _generar_token(rol="admin")

    resp = client_sin_auth.get("/api/v1/productos", headers=_auth_header(token))

    assert resp.status_code == 200


def test_listar_productos_con_rol_empleado_tambien_funciona(client_sin_auth):
    token = _generar_token(rol="empleado")

    resp = client_sin_auth.get("/api/v1/productos", headers=_auth_header(token))

    assert resp.status_code == 200


def test_token_con_firma_invalida_devuelve_401(client_sin_auth):
    token_valido = _generar_token()
    token_alterado = token_valido[:-5] + "XXXXX"  # corrompe la firma

    resp = client_sin_auth.get("/api/v1/productos", headers=_auth_header(token_alterado))

    assert resp.status_code == 401


def test_token_expirado_devuelve_401(client_sin_auth):
    token_expirado = _generar_token(segundos_para_expirar=-10)  # ya vencido

    resp = client_sin_auth.get("/api/v1/productos", headers=_auth_header(token_expirado))

    assert resp.status_code == 401


def test_token_de_tipo_refresh_no_sirve_para_endpoints_protegidos(client_sin_auth):
    """Un refresh token no debe poder usarse como si fuera un access token."""
    token_refresh = _generar_token(tipo="refresh")

    resp = client_sin_auth.get("/api/v1/productos", headers=_auth_header(token_refresh))

    assert resp.status_code == 401


def test_crear_producto_sin_token_devuelve_401(client_sin_auth, producto_payload):
    resp = client_sin_auth.post("/api/v1/productos", json=producto_payload)

    assert resp.status_code == 401


def test_eliminar_producto_con_rol_empleado_devuelve_403(client_sin_auth, producto_payload):
    """DELETE está restringido a admin (issue #7): empleado debe recibir 403."""
    token_admin = _generar_token(rol="admin")
    creado = client_sin_auth.post(
        "/api/v1/productos", json=producto_payload, headers=_auth_header(token_admin)
    ).json()

    token_empleado = _generar_token(rol="empleado")
    resp = client_sin_auth.delete(
        f"/api/v1/productos/{creado['id_producto']}", headers=_auth_header(token_empleado)
    )

    assert resp.status_code == 403


def test_eliminar_producto_con_rol_admin_funciona(client_sin_auth, producto_payload):
    token_admin = _generar_token(rol="admin")
    creado = client_sin_auth.post(
        "/api/v1/productos", json=producto_payload, headers=_auth_header(token_admin)
    ).json()

    resp = client_sin_auth.delete(
        f"/api/v1/productos/{creado['id_producto']}", headers=_auth_header(token_admin)
    )

    assert resp.status_code == 204


def test_token_revocado_en_redis_es_rechazado(client_sin_auth, monkeypatch):
    """
    Simula que el jti del token fue revocado (logout) consultando la
    blacklist: parchea _token_esta_revocado para simular que Redis dice
    que sí está revocado, sin depender de tener Redis corriendo en el
    entorno de test de productos.
    """
    from app.core import jwt_auth

    monkeypatch.setattr(jwt_auth, "_token_esta_revocado", lambda jti: True)

    token = _generar_token()
    resp = client_sin_auth.get("/api/v1/productos", headers=_auth_header(token))

    assert resp.status_code == 401
    assert "revocado" in resp.json()["mensaje"]


def test_si_redis_esta_caido_el_servicio_sigue_funcionando(client_sin_auth, monkeypatch):
    """
    Fail-open documentado en jwt_auth.py: si Redis no responde, no se
    bloquea todo el servicio de productos por eso, y el request sigue
    (la validación de firma/expiración del JWT en sí no se ve afectada).
    """
    from app.core import jwt_auth
    import redis as redis_module

    def redis_caido(jti):
        raise redis_module.RedisError("Connection refused (simulado)")

    monkeypatch.setattr(jwt_auth, "_redis_client", type("FakeRedis", (), {
        "exists": lambda self, key: (_ for _ in ()).throw(redis_module.RedisError("simulado"))
    })())

    token = _generar_token()
    resp = client_sin_auth.get("/api/v1/productos", headers=_auth_header(token))

    assert resp.status_code == 200
