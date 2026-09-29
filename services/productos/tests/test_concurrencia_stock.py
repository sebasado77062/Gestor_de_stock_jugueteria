"""
Test de concurrencia sobre el ajuste de stock.

Consigna (AE1, punto 7 / AE2, profundización): identificar y resolver una
situación de concurrencia, demostrando primero el problema y después la
solución con su verificación.

Escenario: un producto con stock_actual = 1 recibe DOS ventas simultáneas
de 1 unidad cada una. Solo una debería poder concretarse.

Se corre con: docker compose exec productos pytest
(o localmente: cd services/productos && pytest)
"""
import os
import threading
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.db import Base
from app.models.producto import Producto
from app.services import producto_service


@pytest.fixture()
def session_factory():
    """
    Engine SQLite sobre archivo temporal.

    No se usa sqlite:///:memory: + StaticPool porque esa combinación hace
    que todos los hilos compartan una única conexión, y con múltiples
    hilos concurrentes se pisan entre sí. Con un archivo cada sesión abre
    su propia conexión real y SQLite serializa los UPDATE por su cuenta
    (con timeout amplio para evitar 'database is locked').
    """
    archivo_temporal = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo_temporal.close()
    url = f"sqlite:///{archivo_temporal.name}"

    engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield Session
    engine.dispose()
    os.remove(archivo_temporal.name)


def _crear_producto_con_stock(Session, stock_inicial: int) -> int:
    db = Session()
    producto = Producto(
        nombre="Muñeca Bebota",
        marca="Playkids",
        precio_venta=1000.0,
        stock_actual=stock_inicial,
        stock_minimo=1,
        categoria="Muñecas",
    )
    db.add(producto)
    db.commit()
    db.refresh(producto)
    id_producto = producto.id_producto
    db.close()
    return id_producto


def _leer_stock(Session, id_producto: int) -> int:
    db = Session()
    producto = db.query(Producto).filter(Producto.id_producto == id_producto).first()
    stock = producto.stock_actual
    db.close()
    return stock


# ---------------------------------------------------------------------------
# 1) PROBLEMA: ajuste "ingenuo" (leer -> validar en Python -> escribir)
# ---------------------------------------------------------------------------

def _venta_no_atomica(Session, id_producto: int, cantidad: int, barrera: threading.Barrier, resultados: list):
    """
    Reproduce el patrón INCORRECTO: lee el stock, valida en Python, y recién
    ahí escribe. La barrera fuerza que ambos hilos lean ANTES de que
    cualquiera escriba, garantizando el entrelazado del bug.
    """
    db = Session()
    try:
        producto = db.query(Producto).filter(Producto.id_producto == id_producto).first()
        stock_leido = producto.stock_actual

        barrera.wait()  # ambos hilos ya leyeron; ninguno vio la escritura del otro

        if stock_leido >= cantidad:
            producto.stock_actual = stock_leido - cantidad
            db.commit()
            resultados.append("aceptada")
        else:
            resultados.append("rechazada")
    finally:
        db.close()


def test_ajuste_no_atomico_permite_sobreventa(session_factory):
    """
    PROBLEMA: con stock_actual = 1, dos ventas simultáneas de 1 unidad
    deberían resultar en UNA aceptada y UNA rechazada. Con el patrón ingenuo,
    ambos hilos leen stock=1 antes de que ninguno escriba, así que AMBOS
    pasan la validación.

    Resultado incorrecto: se registran 2 ventas de un producto que solo
    tenía 1 unidad (sobreventa), y la segunda escritura "pisa" a la primera
    (lost update).
    """
    Session = session_factory
    id_producto = _crear_producto_con_stock(Session, stock_inicial=1)
    barrera = threading.Barrier(2)
    resultados = []

    hilos = [
        threading.Thread(target=_venta_no_atomica, args=(Session, id_producto, 1, barrera, resultados))
        for _ in range(2)
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    stock_final = _leer_stock(Session, id_producto)

    # BUG: las dos ventas se marcaron como aceptadas para un stock de 1 unidad.
    assert resultados.count("aceptada") == 2
    # Lost update: la segunda escritura pisa a la primera.
    assert stock_final == 0


# ---------------------------------------------------------------------------
# 2) SOLUCIÓN: ajuste atómico (UPDATE ... WHERE stock_actual >= n)
# ---------------------------------------------------------------------------

def _venta_atomica(Session, id_producto: int, delta: int, resultados: list):
    db = Session()
    try:
        resultado = producto_service.ajustar_stock_atomico(db, id_producto, delta)
        resultados.append(resultado)
    finally:
        db.close()


def test_ajuste_atomico_previene_sobreventa(session_factory):
    """
    SOLUCIÓN: mismo escenario (stock_actual = 1, dos ventas simultáneas de
    la última unidad), pero con ajustar_stock_atomico, donde la condición
    "hay stock suficiente" se evalúa dentro del propio UPDATE.

    Verificación: exactamente una venta se acepta ("ok"), la otra se rechaza
    ("sin_stock"), y el stock nunca queda en un valor inconsistente.
    """
    Session = session_factory
    id_producto = _crear_producto_con_stock(Session, stock_inicial=1)
    resultados = []

    hilos = [
        threading.Thread(target=_venta_atomica, args=(Session, id_producto, -1, resultados))
        for _ in range(2)
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    stock_final = _leer_stock(Session, id_producto)

    assert resultados.count("ok") == 1
    assert resultados.count("sin_stock") == 1
    assert stock_final == 0  # nunca negativo, nunca sobrevendido