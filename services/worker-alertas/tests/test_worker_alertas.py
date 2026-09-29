"""
Tests del worker de Alertas: lógica de idempotencia y de evaluación de la
alerta, sin necesitar RabbitMQ ni Productos corriendo.

El payload del evento incluye stock_actual y stock_minimo (snapshot del
producto al momento del movimiento), así que no hace falta mockear
Productos.
"""
import os
import tempfile

import pytest

from alertas import (
    evaluar_y_generar_alerta,
    inicializar_db,
    marcar_como_procesado,
    ya_fue_procesado,
)
from worker import contar_reintentos_previos


@pytest.fixture()
def db_idempotencia():
    archivo_temporal = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo_temporal.close()
    conexion = inicializar_db(archivo_temporal.name)
    yield conexion
    conexion.close()
    os.remove(archivo_temporal.name)


def test_marca_y_detecta_un_movimiento_ya_procesado(db_idempotencia):
    assert ya_fue_procesado(db_idempotencia, id_movimiento=1) is False

    marcar_como_procesado(db_idempotencia, id_movimiento=1, id_producto=10, genero_alerta=True)

    assert ya_fue_procesado(db_idempotencia, id_movimiento=1) is True
    # Un id_movimiento distinto no está afectado.
    assert ya_fue_procesado(db_idempotencia, id_movimiento=2) is False


def test_marcar_como_procesado_es_idempotente(db_idempotencia):
    """Insertar el mismo id_movimiento dos veces no debe fallar (INSERT OR IGNORE)."""
    marcar_como_procesado(db_idempotencia, id_movimiento=1, id_producto=10, genero_alerta=False)
    marcar_como_procesado(db_idempotencia, id_movimiento=1, id_producto=10, genero_alerta=False)
    assert ya_fue_procesado(db_idempotencia, id_movimiento=1) is True


def test_genera_alerta_si_stock_actual_por_debajo_del_minimo():
    payload = {
        "id_movimiento": 1,
        "id_producto": 5,
        "nombre_producto": "Auto a fricción",
        "stock_actual": 2,
        "stock_minimo": 5,
    }
    assert evaluar_y_generar_alerta(payload) is True


def test_no_genera_alerta_si_hay_stock_suficiente():
    payload = {
        "id_movimiento": 2,
        "id_producto": 5,
        "nombre_producto": "Auto a fricción",
        "stock_actual": 20,
        "stock_minimo": 5,
    }
    assert evaluar_y_generar_alerta(payload) is False


def test_contar_reintentos_previos_sin_header():
    class PropiedadesFalsas:
        headers = None

    assert contar_reintentos_previos(PropiedadesFalsas()) == 0


def test_contar_reintentos_previos_con_x_death():
    class PropiedadesFalsas:
        headers = {"x-death": [{"count": 1}, {"count": 1}]}

    assert contar_reintentos_previos(PropiedadesFalsas()) == 2