"""
Fixtures compartidas para los tests HTTP del servicio movimientos
(las usa tests/test_bateria_minima.py).

Qué reemplaza cada fixture, para que los tests no necesiten Docker, Redis,
RabbitMQ ni el servicio de Productos corriendo:

    - Base de datos  -> SQLite en un archivo temporal, nueva en cada test.
                        (Es archivo y no ":memory:" porque el test de carga
                        usa varios hilos a la vez y cada uno necesita su
                        propia sesión sobre la misma base.)
    - Autenticación  -> se simula un usuario autenticado con dependency_overrides
                        (no se generan JWT reales ni se consulta Redis).
    - Productos      -> se mockea productos_client.ajustar_stock.
    - RabbitMQ       -> se mockea publisher.publicar_movimiento_registrado.

Los tests que ya existían (test_movimientos.py, test_idempotencia.py) no
usan este archivo: arman sus propias fixtures.
"""
import os

# Estas variables se leen al IMPORTAR app.main, así que van antes del import.
os.environ.setdefault("AUTH_SECRET_KEY", "clave-de-test-no-usar-en-produccion")
# app.main crea las tablas al importarse: se apunta a una base en memoria para
# que importar la app en los tests nunca toque (ni exija) el archivo real
# data/movimientos.db.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.jwt_auth import obtener_token_y_payload
from app.database.db import Base, get_db
from app.main import app

TOKEN_FALSO = "token-de-prueba-no-valido"
PAYLOAD_ADMIN_FALSO = {
    "sub": "1",
    "email": "admin@test.com",
    "rol": "admin",
    "tipo": "access",
    "jti": "jti-fake-admin",
}

# Lo que devolvería Productos al aceptar un ajuste de stock.
PRODUCTO_AJUSTADO_FALSO = {
    "id_producto": 1,
    "nombre": "Muñeca Bebota",
    "stock_actual": 9,
    "stock_minimo": 5,
}


@pytest.fixture()
def session_factory():
    """Fábrica de sesiones sobre una base SQLite temporal, vacía en cada test."""
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    engine = create_engine(
        f"sqlite:///{archivo.name}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)

    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)

    engine.dispose()
    os.remove(archivo.name)


@pytest.fixture()
def productos_mock():
    """Productos acepta siempre el ajuste de stock. Devuelve el mock para inspeccionarlo."""
    with patch(
        "app.services.movimiento_service.productos_client.ajustar_stock",
        return_value=PRODUCTO_AJUSTADO_FALSO,
    ) as mock:
        yield mock


@pytest.fixture()
def publisher_mock():
    """RabbitMQ reemplazado por un mock: no se publica nada de verdad."""
    with patch("app.messaging.publisher.publicar_movimiento_registrado") as mock:
        yield mock


@pytest.fixture()
def client(session_factory, productos_mock, publisher_mock):
    """
    TestClient de la app real, con una sesión de base NUEVA por request (como
    en producción) y la autenticación simulada como admin.
    """

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[obtener_token_y_payload] = lambda: (
        TOKEN_FALSO,
        PAYLOAD_ADMIN_FALSO,
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def movimiento_payload():
    """Payload válido mínimo para registrar un movimiento."""
    return {
        "categoria_movimiento": "venta",
        "cantidad_movida": 2,
        "id_producto": 1,
        "dni_cliente": "30123456",
        "dni_empleado": "20111222",
    }
