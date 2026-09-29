"""
Test de idempotencia del POST /api/v1/movimientos.

Verifica que, ante dos requests con la misma Idempotency-Key (simulando un
reintento del cliente por timeout, doble clic, etc.), el stock se ajusta
UNA sola vez y ambas respuestas devuelven el mismo movimiento.

No requiere Redis, RabbitMQ ni Productos corriendo: se reemplazan por
dobles de prueba (fakes/mocks) en memoria. La autenticación también se
simula con credenciales ficticias (el router solo lee el token del tuple
y lo pasa al service; el service lo reenvía al cliente HTTP mockeado).
"""
import os
import tempfile
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.db import Base
from app.routers.movimiento_router import crear_movimiento
from app.schemas.movimiento import MovimientoCreate

TOKEN_FALSO = "token-de-prueba-no-valido"
CREDENCIALES_FALSAS = (TOKEN_FALSO, {"sub": "1", "rol": "admin", "tipo": "access", "jti": "jti-test"})

# Snapshot del producto tal como lo devolvería el endpoint de ajustar-stock:
# incluye lo necesario para que el publisher pueda construir el evento.
PRODUCTO_AJUSTADO_FALSO = {
    "id_producto": 1,
    "nombre": "Muñeca Bebota",
    "stock_actual": 9,
    "stock_minimo": 5,
}


class RedisFalso:
    """Reemplaza a app.idempotencia.redis_client con un dict en memoria."""

    def __init__(self):
        self._almacen = {}

    def obtener_id_movimiento_previo(self, idempotency_key):
        return self._almacen.get(idempotency_key)

    def registrar_idempotency_key(self, idempotency_key, id_movimiento):
        if idempotency_key:
            self._almacen[idempotency_key] = id_movimiento


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


def test_misma_idempotency_key_no_duplica_el_movimiento(SessionLocal):
    db = SessionLocal()
    redis_falso = RedisFalso()
    datos = MovimientoCreate(categoria_movimiento="venta", cantidad_movida=1, id_producto=1)

    with (
        patch("app.routers.movimiento_router.redis_client.obtener_id_movimiento_previo", redis_falso.obtener_id_movimiento_previo),
        patch("app.routers.movimiento_router.redis_client.registrar_idempotency_key", redis_falso.registrar_idempotency_key),
        patch("app.routers.movimiento_router.publisher.publicar_movimiento_registrado"),
        patch("app.services.movimiento_service.productos_client.ajustar_stock") as mock_ajustar,
    ):
        mock_ajustar.return_value = PRODUCTO_AJUSTADO_FALSO

        primera_respuesta = crear_movimiento(
            datos, db=db, credenciales=CREDENCIALES_FALSAS, idempotency_key="clave-abc-123"
        )
        segunda_respuesta = crear_movimiento(
            datos, db=db, credenciales=CREDENCIALES_FALSAS, idempotency_key="clave-abc-123"
        )

        # El stock solo se ajustó UNA vez, a pesar de las dos solicitudes.
        mock_ajustar.assert_called_once()

    # Ambas respuestas corresponden al mismo movimiento (no se duplicó).
    assert primera_respuesta.id_movimiento == segunda_respuesta.id_movimiento
    db.close()


def test_idempotency_key_distinta_si_registra_un_movimiento_nuevo(SessionLocal):
    db = SessionLocal()
    redis_falso = RedisFalso()
    datos = MovimientoCreate(categoria_movimiento="venta", cantidad_movida=1, id_producto=1)

    with (
        patch("app.routers.movimiento_router.redis_client.obtener_id_movimiento_previo", redis_falso.obtener_id_movimiento_previo),
        patch("app.routers.movimiento_router.redis_client.registrar_idempotency_key", redis_falso.registrar_idempotency_key),
        patch("app.routers.movimiento_router.publisher.publicar_movimiento_registrado"),
        patch("app.services.movimiento_service.productos_client.ajustar_stock") as mock_ajustar,
    ):
        mock_ajustar.return_value = PRODUCTO_AJUSTADO_FALSO

        primera_respuesta = crear_movimiento(
            datos, db=db, credenciales=CREDENCIALES_FALSAS, idempotency_key="clave-1"
        )
        segunda_respuesta = crear_movimiento(
            datos, db=db, credenciales=CREDENCIALES_FALSAS, idempotency_key="clave-2"
        )

        assert mock_ajustar.call_count == 2

    assert primera_respuesta.id_movimiento != segunda_respuesta.id_movimiento
    db.close()