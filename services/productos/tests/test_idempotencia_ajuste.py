"""
Tests de la idempotencia del ajuste de stock (PATCH /ajustar-stock con el
header Idempotency-Key).

Problema que resuelven: Movimientos puede reintentar (o recuperar un
movimiento que quedó pendiente) el mismo ajuste. Sin idempotencia en
Productos, un reintento descontaría el stock dos veces. Con la clave, el
cambio de stock y el registro de la clave se confirman en UNA transacción.

Incluye una prueba con hilos reales y archivo SQLite (como
test_concurrencia_stock.py) para el caso de dos pedidos simultáneos con la
misma clave.
"""
import os
import tempfile
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.db import Base
from app.models.ajuste_stock import AjusteStock
from app.models.producto import Producto
from app.services import producto_service


def _crear(client, producto_payload, stock):
    return client.post("/api/v1/productos", json={**producto_payload, "stock_actual": stock}).json()["id_producto"]


def _ajustar(client, id_producto, delta, clave=None):
    headers = {"Idempotency-Key": clave} if clave else {}
    return client.patch(f"/api/v1/productos/{id_producto}/ajustar-stock", json={"delta": delta}, headers=headers)


def _stock(client, id_producto):
    return client.get(f"/api/v1/productos/{id_producto}").json()["stock_actual"]


def test_misma_clave_no_vuelve_a_descontar_el_stock(client, producto_payload):
    id_producto = _crear(client, producto_payload, stock=10)

    primera = _ajustar(client, id_producto, -3, clave="op-1")
    segunda = _ajustar(client, id_producto, -3, clave="op-1")

    assert primera.status_code == 200
    assert segunda.status_code == 200
    assert _stock(client, id_producto) == 7  # descontó UNA vez, no dos


def test_claves_distintas_si_aplican_cada_ajuste(client, producto_payload):
    id_producto = _crear(client, producto_payload, stock=10)

    _ajustar(client, id_producto, -3, clave="op-1")
    _ajustar(client, id_producto, -3, clave="op-2")

    assert _stock(client, id_producto) == 4


def test_sin_clave_el_comportamiento_no_cambia(client, producto_payload):
    id_producto = _crear(client, producto_payload, stock=10)

    _ajustar(client, id_producto, -2)
    _ajustar(client, id_producto, -2)

    assert _stock(client, id_producto) == 6


def test_la_clave_reutilizada_con_otra_cantidad_es_422_y_no_modifica_stock(client, producto_payload):
    id_producto = _crear(client, producto_payload, stock=10)
    _ajustar(client, id_producto, -3, clave="op-1")

    resp = _ajustar(client, id_producto, -5, clave="op-1")

    assert resp.status_code == 422
    assert resp.json()["error"] is True
    assert _stock(client, id_producto) == 7


def test_la_clave_reutilizada_con_otro_producto_es_422(client, producto_payload):
    a = _crear(client, producto_payload, stock=10)
    b = _crear(client, producto_payload, stock=10)
    _ajustar(client, a, -1, clave="op-1")

    resp = _ajustar(client, b, -1, clave="op-1")

    assert resp.status_code == 422
    assert _stock(client, b) == 10


def test_un_ajuste_rechazado_por_falta_de_stock_no_consume_la_clave(client, producto_payload):
    """Si se rechaza (409) no queda registrada: tras reponer stock, el mismo reintento sí aplica."""
    id_producto = _crear(client, producto_payload, stock=2)

    rechazado = _ajustar(client, id_producto, -5, clave="op-1")
    assert rechazado.status_code == 409
    assert _stock(client, id_producto) == 2

    _ajustar(client, id_producto, +10, clave="reposicion")
    reintento = _ajustar(client, id_producto, -5, clave="op-1")

    assert reintento.status_code == 200
    assert _stock(client, id_producto) == 7


def test_producto_inexistente_con_clave_es_404(client):
    assert _ajustar(client, 9999, -1, clave="op-1").status_code == 404


def test_el_ajuste_y_la_clave_se_guardan_juntos(client, producto_payload, db_session):
    id_producto = _crear(client, producto_payload, stock=10)

    _ajustar(client, id_producto, -4, clave="op-1")

    registro = db_session.get(AjusteStock, "op-1")
    assert registro is not None
    assert (registro.id_producto, registro.delta) == (id_producto, -4)


# ---------------------------------------------------------------------------
# Concurrencia real: dos pedidos simultáneos con la MISMA clave
# ---------------------------------------------------------------------------
@pytest.fixture()
def session_factory():
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()
    engine = create_engine(
        f"sqlite:///{archivo.name}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    engine.dispose()
    os.remove(archivo.name)


def test_dos_pedidos_simultaneos_con_la_misma_clave_aplican_un_solo_ajuste(session_factory):
    db = session_factory()
    producto = Producto(nombre="Auto", precio_venta=10.0, stock_actual=10, stock_minimo=1)
    db.add(producto)
    db.commit()
    id_producto = producto.id_producto
    db.close()

    resultados, errores = [], []
    barrera = threading.Barrier(2)

    def pedido():
        sesion = session_factory()
        try:
            barrera.wait()
            resultados.append(producto_service.ajustar_stock_atomico(sesion, id_producto, -3, clave="op-concurrente"))
        except Exception as error:  # pragma: no cover - solo para diagnosticar
            errores.append(error)
        finally:
            sesion.close()

    hilos = [threading.Thread(target=pedido) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert errores == []
    assert resultados == ["ok", "ok"]  # ambos ven éxito (uno aplicó, el otro fue reintento)

    db = session_factory()
    assert db.get(Producto, id_producto).stock_actual == 7  # se descontó UNA sola vez
    assert db.query(AjusteStock).count() == 1
    db.close()


# ---------------------------------------------------------------------------
# GET /ajustes/{clave}: ¿esta operación llegó a aplicarse? (para reconciliar)
# ---------------------------------------------------------------------------
def test_consultar_un_ajuste_aplicado_devuelve_200_con_sus_datos(client, producto_payload):
    id_producto = _crear(client, producto_payload, stock=10)
    _ajustar(client, id_producto, -4, clave="op-1")

    resp = client.get("/api/v1/productos/ajustes/op-1")

    assert resp.status_code == 200
    cuerpo = resp.json()
    assert (cuerpo["clave"], cuerpo["id_producto"], cuerpo["delta"]) == ("op-1", id_producto, -4)


def test_consultar_un_ajuste_que_nunca_se_aplico_devuelve_404(client):
    resp = client.get("/api/v1/productos/ajustes/no-existe")

    assert resp.status_code == 404


def test_un_ajuste_rechazado_por_stock_no_figura_como_aplicado(client, producto_payload):
    id_producto = _crear(client, producto_payload, stock=1)
    _ajustar(client, id_producto, -5, clave="op-1")  # 409

    assert client.get("/api/v1/productos/ajustes/op-1").status_code == 404
