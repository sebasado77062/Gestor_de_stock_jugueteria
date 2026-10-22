"""
Tests del servicio de Movimientos: registro básico, cálculo de delta por
categoría, y propagación de errores del servicio de Productos.

No requieren Productos, Redis ni RabbitMQ corriendo: se mockea el cliente
HTTP de Productos (la saga completa, con fallos y reintentos, se prueba en
test_saga_consistencia.py). El token de prueba es ficticio (el service
no lo valida, solo lo propaga).
"""
from unittest.mock import ANY, patch

import pytest

from app.clients.productos_client import ProductosClientError
from app.schemas.movimiento import MovimientoCreate
from app.services import movimiento_service

TOKEN_FALSO = "token-de-prueba-no-valido"


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

    mock_ajustar.assert_called_once_with(1, delta_esperado, TOKEN_FALSO, clave=ANY)
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