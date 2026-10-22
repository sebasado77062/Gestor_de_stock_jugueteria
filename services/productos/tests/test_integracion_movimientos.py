"""
Tests de la integración real de `productos` con `movimientos`:

1. tiene_movimientos_asociados ya no es un placeholder: el DELETE consulta al
   servicio de Movimientos (con el token del admin) y decide entre borrado
   físico (204) o baja lógica (200).
2. Si Movimientos no responde, el DELETE falla CERRADO (503) y no toca nada.
3. El cliente HTTP (movimientos_client) traduce bien las respuestas de error.
4. Modo local: la revocación de tokens se consulta a auth cuando
   REDIS_URL=memory:// y AUTH_SERVICE_URL está definida.
"""
import httpx
import pytest

from app.clients import movimientos_client
from app.clients.movimientos_client import MovimientosClientError
from app.core import jwt_auth

# El fixture autouse `movimientos_stub` (conftest.py) reemplaza
# movimientos_client.contar_movimientos en cada test. Para probar el cliente
# REAL se usa esta referencia, capturada al importar el módulo (antes de que
# ningún fixture la reemplace).
contar_movimientos_real = movimientos_client.contar_movimientos


# --- DELETE usa la consulta real ------------------------------------------
def test_delete_consulta_a_movimientos_con_el_id_del_producto(client, producto_payload, movimientos_stub):
    creado = client.post("/api/v1/productos", json=producto_payload).json()

    client.delete(f"/api/v1/productos/{creado['id_producto']}")

    assert [id_producto for id_producto, _ in movimientos_stub.llamadas] == [creado["id_producto"]]


def test_delete_con_historial_hace_baja_logica(client, producto_payload, movimientos_stub):
    movimientos_stub.cantidad = 2
    creado = client.post("/api/v1/productos", json=producto_payload).json()

    resp = client.delete(f"/api/v1/productos/{creado['id_producto']}")

    assert resp.status_code == 200
    assert resp.json()["activo"] is False


def test_delete_sin_historial_borra_fisicamente(client, producto_payload, movimientos_stub):
    movimientos_stub.cantidad = 0
    creado = client.post("/api/v1/productos", json=producto_payload).json()

    resp = client.delete(f"/api/v1/productos/{creado['id_producto']}")

    assert resp.status_code == 204
    assert client.get(f"/api/v1/productos/{creado['id_producto']}").status_code == 404


def test_delete_de_producto_inexistente_es_404_y_no_consulta_a_movimientos(client, movimientos_stub):
    resp = client.delete("/api/v1/productos/9999")

    assert resp.status_code == 404
    assert movimientos_stub.llamadas == []


def test_delete_falla_cerrado_si_movimientos_no_responde(client, producto_payload, movimientos_stub):
    movimientos_stub.error = MovimientosClientError(503, "No se pudo conectar con el servicio de Movimientos.")
    creado = client.post("/api/v1/productos", json=producto_payload).json()

    resp = client.delete(f"/api/v1/productos/{creado['id_producto']}")

    assert resp.status_code == 503
    assert resp.json()["error"] is True
    assert "No se eliminó" in resp.json()["mensaje"]
    # El producto sigue existiendo y activo: no se borró ni se dio de baja.
    existente = client.get(f"/api/v1/productos/{creado['id_producto']}")
    assert existente.status_code == 200
    assert existente.json()["activo"] is True


def test_el_delete_reenvia_el_token_del_usuario_a_movimientos(client_sin_auth, producto_payload, movimientos_stub):
    from jose import jwt

    token = jwt.encode(
        {"sub": "1", "email": "a@a.com", "rol": "admin", "tipo": "access", "jti": "j-1", "exp": 4102444800},
        jwt_auth.SECRET_KEY,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    creado = client_sin_auth.post("/api/v1/productos", json=producto_payload, headers=headers).json()

    client_sin_auth.delete(f"/api/v1/productos/{creado['id_producto']}", headers=headers)

    assert movimientos_stub.llamadas == [(creado["id_producto"], token)]


# --- movimientos_client (HTTP real simulado) -------------------------------
class _Respuesta:
    def __init__(self, status_code, cuerpo=None):
        self.status_code = status_code
        self._cuerpo = cuerpo

    def json(self):
        if self._cuerpo is None:
            raise ValueError("sin cuerpo")
        return self._cuerpo


@pytest.fixture()
def http_falso(monkeypatch):
    """Reemplaza httpx.get dentro del cliente; el test decide qué responde o qué lanza."""
    estado = {"respuesta": _Respuesta(200, {"cantidad": 0}), "excepcion": None, "pedidos": []}

    def get(url, headers=None, timeout=None):
        estado["pedidos"].append((url, headers, timeout))
        if estado["excepcion"]:
            raise estado["excepcion"]
        return estado["respuesta"]

    monkeypatch.setattr(movimientos_client.httpx, "get", get)
    return estado


def test_cliente_arma_la_url_y_manda_el_token(http_falso):
    http_falso["respuesta"] = _Respuesta(200, {"id_producto": 7, "cantidad": 4})

    assert contar_movimientos_real(7, "tok") == 4

    url, headers, _ = http_falso["pedidos"][0]
    assert url.endswith("/api/v1/productos/7/movimientos/cantidad")
    assert headers == {"Authorization": "Bearer tok"}


@pytest.mark.parametrize(
    "excepcion",
    [httpx.ConnectError("rechazada"), httpx.ReadTimeout("lento")],
)
def test_cliente_traduce_caidas_y_timeouts_a_503(http_falso, excepcion):
    http_falso["excepcion"] = excepcion

    with pytest.raises(MovimientosClientError) as error:
        contar_movimientos_real(1, "tok")

    assert error.value.status_code == 503


@pytest.mark.parametrize("status_http,esperado", [(401, 401), (403, 403), (404, 502), (500, 502)])
def test_cliente_traduce_errores_http(http_falso, status_http, esperado):
    http_falso["respuesta"] = _Respuesta(status_http)

    with pytest.raises(MovimientosClientError) as error:
        contar_movimientos_real(1, "tok")

    assert error.value.status_code == esperado


def test_cliente_rechaza_una_respuesta_con_formato_inesperado(http_falso):
    http_falso["respuesta"] = _Respuesta(200, {"otra_cosa": 1})

    with pytest.raises(MovimientosClientError) as error:
        contar_movimientos_real(1, "tok")

    assert error.value.status_code == 502


# --- Modo local: revocación consultada a auth ------------------------------
def test_modo_local_consulta_la_revocacion_a_auth(monkeypatch):
    pedidos = []

    def get(url, timeout=None):
        pedidos.append(url)
        return _Respuesta(200, {"revocado": True})

    monkeypatch.setattr(jwt_auth, "_REVOCACION_VIA_AUTH", True)
    monkeypatch.setattr(jwt_auth, "AUTH_SERVICE_URL", "http://auth.local:8001")
    monkeypatch.setattr(jwt_auth.httpx, "get", get)

    assert jwt_auth._token_esta_revocado("jti-123") is True
    assert pedidos == ["http://auth.local:8001/api/v1/auth/tokens/jti-123/revocado"]


def test_modo_local_token_no_revocado(monkeypatch):
    monkeypatch.setattr(jwt_auth, "_REVOCACION_VIA_AUTH", True)
    monkeypatch.setattr(jwt_auth.httpx, "get", lambda url, timeout=None: _Respuesta(200, {"revocado": False}))

    assert jwt_auth._token_esta_revocado("jti-123") is False


@pytest.mark.parametrize("falla", [httpx.ConnectError("auth caído"), httpx.ReadTimeout("lento")])
def test_modo_local_si_auth_no_responde_se_deja_pasar_fail_open(monkeypatch, falla):
    def get(url, timeout=None):
        raise falla

    monkeypatch.setattr(jwt_auth, "_REVOCACION_VIA_AUTH", True)
    monkeypatch.setattr(jwt_auth.httpx, "get", get)

    assert jwt_auth._token_esta_revocado("jti-123") is False


def test_modo_local_un_token_revocado_en_auth_es_rechazado_con_401(client_sin_auth, monkeypatch):
    from jose import jwt

    token = jwt.encode(
        {"sub": "1", "email": "a@a.com", "rol": "admin", "tipo": "access", "jti": "j-revocado", "exp": 4102444800},
        jwt_auth.SECRET_KEY,
        algorithm="HS256",
    )
    monkeypatch.setattr(jwt_auth, "_REVOCACION_VIA_AUTH", True)
    monkeypatch.setattr(jwt_auth.httpx, "get", lambda url, timeout=None: _Respuesta(200, {"revocado": True}))

    resp = client_sin_auth.get("/api/v1/productos", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert "revocado" in resp.json()["mensaje"].lower()


def test_con_redis_compartido_no_se_consulta_a_auth(monkeypatch):
    """Fuera del modo local (REDIS_URL real) la revocación sigue yendo solo a Redis."""
    monkeypatch.setattr(jwt_auth, "_REVOCACION_VIA_AUTH", False)

    def no_deberia_llamarse(*a, **k):
        raise AssertionError("no debe consultar a auth con Redis compartido")

    monkeypatch.setattr(jwt_auth.httpx, "get", no_deberia_llamarse)
    monkeypatch.setattr(jwt_auth._redis_client, "exists", lambda clave: 0)

    assert jwt_auth._token_esta_revocado("jti-123") is False
