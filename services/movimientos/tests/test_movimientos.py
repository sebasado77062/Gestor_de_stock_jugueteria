"""
Tests del servicio de Movimientos: registro básico, cálculo de delta por
categoría, y propagación de errores del servicio de Productos.

No requieren Productos, Redis ni RabbitMQ corriendo: se mockea el cliente
HTTP (app.clients.productos_client) y se pasa un token de prueba ficticio
(el service no valida el token, solo lo propaga al cliente).
"""
import os
import tempfile
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.clients.productos_client import ProductosClientError
from app.database.db import Base
from app.schemas.movimiento import MovimientoCreate
from app.services import movimiento_service

TOKEN_FALSO = "token-de-prueba-no-valido"


@pytest.fixture()
def SessionLocal():
    archivo_temporal = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo_temporal.close()
    url = f"sqlite:///{archivo_temporal.name}"

    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    yield Session

    engine.dispose()
    os.remove(archivo_temporal.name)


@pytest.mark.parametrize(
    "categoria,cantidad,delta_esperado",
    [
        ("venta", 3, -3),
        ("estropeo", 2, -2),
        ("ingreso", 5, 5),
        ("devolucion", 1, 1),
    ],
)
def test_calcula_el_delta_segun_la_categoria(SessionLocal, categoria, cantidad, delta_esperado):
    """El signo del ajuste de stock depende de la categoría del movimiento."""
    db = SessionLocal()
    datos = MovimientoCreate(categoria_movimiento=categoria, cantidad_movida=cantidad, id_producto=1)

    with patch("app.services.movimiento_service.productos_client.ajustar_stock") as mock_ajustar:
        mock_ajustar.return_value = {"id_producto": 1, "stock_actual": 10}
        movimiento_service.registrar_movimiento(db, datos, TOKEN_FALSO)

    mock_ajustar.assert_called_once_with(1, delta_esperado, TOKEN_FALSO)
    db.close()


def test_registra_el_movimiento_solo_si_productos_acepta_el_ajuste(SessionLocal):
    """Si Productos devuelve stock insuficiente (409), no debe quedar un movimiento huérfano."""
    db = SessionLocal()
    datos = MovimientoCreate(categoria_movimiento="venta", cantidad_movida=100, id_producto=1)

    with patch("app.services.movimiento_service.productos_client.ajustar_stock") as mock_ajustar:
        mock_ajustar.side_effect = ProductosClientError(409, "Stock insuficiente para registrar este movimiento.")
        with pytest.raises(ProductosClientError):
            movimiento_service.registrar_movimiento(db, datos, TOKEN_FALSO)

    assert movimiento_service.listar_movimientos(db) == []
    db.close()


def test_lista_movimientos_por_producto(SessionLocal):
    db = SessionLocal()
    with patch("app.services.movimiento_service.productos_client.ajustar_stock") as mock_ajustar:
        mock_ajustar.return_value = {"id_producto": 1, "stock_actual": 9}
        movimiento_service.registrar_movimiento(
            db,
            MovimientoCreate(categoria_movimiento="venta", cantidad_movida=1, id_producto=1),
            TOKEN_FALSO,
        )
        movimiento_service.registrar_movimiento(
            db,
            MovimientoCreate(categoria_movimiento="venta", cantidad_movida=1, id_producto=2),
            TOKEN_FALSO,
        )

    movimientos_producto_1 = movimiento_service.listar_movimientos_por_producto(db, 1)
    assert len(movimientos_producto_1) == 1
    assert movimientos_producto_1[0].id_producto == 1
    db.close()