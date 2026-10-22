"""
Tests de la API HTTP de Movimientos (TestClient), con JWT reales firmados con
la AUTH_SECRET_KEY de prueba y Productos simulado (ver conftest.py).

Cubren el contrato visible para el cliente: códigos de estado, mensajes,
header Idempotency-Key, estado `pendiente`/`confirmado`, el conteo que usa
Productos antes de borrar, y la reconciliación (solo admin).
"""
import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app import main as main_module
from app.core import jwt_auth
from app.database.db import get_db
from app.main import app

_contador_jti = iter(range(1, 10_000))


def _token(rol="empleado"):
    claims = {
        "sub": "1",
        "email": f"{rol}@test.com",
        "rol": rol,
        "tipo": "access",
        "jti": f"jti-test-{next(_contador_jti)}",
        "exp": 4102444800,  # año 2100
    }
    return jwt.encode(claims, jwt_auth.SECRET_KEY, algorithm=jwt_auth.ALGORITHM)


def _headers(rol="empleado", clave=None):
    headers = {"Authorization": f"Bearer {_token(rol)}"}
    if clave:
        headers["Idempotency-Key"] = clave
    return headers


VENTA = {"categoria_movimiento": "venta", "cantidad_movida": 3, "id_producto": 1}


@pytest.fixture()
def client(SessionLocal, productos, monkeypatch):
    # No tocar la base real al arrancar, ni consultar Redis para la blacklist.
    monkeypatch.setattr(main_module, "inicializar_base", lambda: None)
    monkeypatch.setattr(jwt_auth, "_token_esta_revocado", lambda jti: False)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- registro ---------------------------------------------------------------
def test_post_devuelve_201_con_el_movimiento_confirmado(client, productos):
    resp = client.post("/api/v1/movimientos", json=VENTA, headers=_headers())

    assert resp.status_code == 201
    cuerpo = resp.json()
    assert cuerpo["estado"] == "confirmado"
    assert (cuerpo["categoria_movimiento"], cuerpo["cantidad_movida"]) == ("venta", 3)
    assert productos.stock[1] == 7


def test_post_sin_token_devuelve_401(client):
    assert client.post("/api/v1/movimientos", json=VENTA).status_code == 401


def test_post_con_datos_invalidos_devuelve_400(client):
    resp = client.post("/api/v1/movimientos", json={**VENTA, "cantidad_movida": 0}, headers=_headers())

    assert resp.status_code == 400


def test_stock_insuficiente_devuelve_409_y_no_deja_nada_registrado(client, productos):
    resp = client.post("/api/v1/movimientos", json={**VENTA, "cantidad_movida": 99}, headers=_headers())

    assert resp.status_code == 409
    assert "pendiente" not in resp.json()["mensaje"]  # es un rechazo definitivo
    assert client.get("/api/v1/movimientos", headers=_headers()).json() == []


def test_producto_inexistente_devuelve_404(client):
    resp = client.post("/api/v1/movimientos", json={**VENTA, "id_producto": 999}, headers=_headers())

    assert resp.status_code == 404


# --- idempotencia -------------------------------------------------------------
def test_misma_clave_devuelve_el_mismo_movimiento_y_ajusta_una_vez(client, productos):
    a = client.post("/api/v1/movimientos", json=VENTA, headers=_headers(clave="k1"))
    b = client.post("/api/v1/movimientos", json=VENTA, headers=_headers(clave="k1"))

    assert a.status_code == b.status_code == 201
    assert a.json()["id_movimiento"] == b.json()["id_movimiento"]
    assert productos.stock[1] == 7


def test_clave_reutilizada_con_otro_contenido_devuelve_422(client, productos):
    client.post("/api/v1/movimientos", json=VENTA, headers=_headers(clave="k1"))

    resp = client.post("/api/v1/movimientos", json={**VENTA, "cantidad_movida": 5}, headers=_headers(clave="k1"))

    assert resp.status_code == 422
    assert "Idempotency-Key" in resp.json()["mensaje"]
    assert productos.stock[1] == 7


# --- pendientes y recuperación ---------------------------------------------------
def test_si_productos_no_responde_devuelve_503_avisa_que_quedo_pendiente_y_el_reintento_lo_completa(client, productos):
    productos.caido = True

    caido = client.post("/api/v1/movimientos", json=VENTA, headers=_headers(clave="k1"))

    assert caido.status_code == 503
    assert "pendiente de confirmación" in caido.json()["mensaje"]
    [pendiente] = client.get("/api/v1/movimientos", headers=_headers()).json()
    assert pendiente["estado"] == "pendiente"

    productos.caido = False
    reintento = client.post("/api/v1/movimientos", json=VENTA, headers=_headers(clave="k1"))

    assert reintento.status_code == 201
    assert reintento.json()["estado"] == "confirmado"
    assert reintento.json()["id_movimiento"] == pendiente["id_movimiento"]
    assert productos.stock[1] == 7


# --- consultas -----------------------------------------------------------------
def test_listados_y_conteo_por_producto(client):
    client.post("/api/v1/movimientos", json=VENTA, headers=_headers())
    client.post("/api/v1/movimientos", json=VENTA, headers=_headers())

    assert len(client.get("/api/v1/movimientos", headers=_headers()).json()) == 2
    assert len(client.get("/api/v1/productos/1/movimientos", headers=_headers()).json()) == 2
    assert client.get("/api/v1/productos/1/movimientos/cantidad", headers=_headers()).json() == {
        "id_producto": 1,
        "cantidad": 2,
    }
    assert client.get("/api/v1/productos/2/movimientos/cantidad", headers=_headers()).json()["cantidad"] == 0


def test_el_conteo_exige_autenticacion(client):
    assert client.get("/api/v1/productos/1/movimientos/cantidad").status_code == 401


# --- reconciliación -------------------------------------------------------------
def test_reconciliar_es_solo_para_admin(client):
    resp = client.post("/api/v1/movimientos/reconciliar", headers=_headers("empleado"))

    assert resp.status_code == 403


def test_reconciliar_resuelve_los_pendientes(client, productos):
    productos.perder_respuesta = True  # el stock se descuenta pero la respuesta se pierde
    client.post("/api/v1/movimientos", json=VENTA, headers=_headers(clave="k1"))
    productos.perder_respuesta = False

    resp = client.post("/api/v1/movimientos/reconciliar?antiguedad_minima_segundos=0", headers=_headers("admin"))

    assert resp.status_code == 200
    assert resp.json() == {"revisados": 1, "confirmados": 1, "descartados": 0, "sin_resolver": 0}
    assert client.get("/api/v1/movimientos", headers=_headers()).json()[0]["estado"] == "confirmado"
    assert productos.stock[1] == 7  # reconciliar no volvió a descontar


def test_reconciliar_rechaza_una_antiguedad_negativa(client):
    resp = client.post("/api/v1/movimientos/reconciliar?antiguedad_minima_segundos=-1", headers=_headers("admin"))

    assert resp.status_code == 400


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "service": "movimientos"}
