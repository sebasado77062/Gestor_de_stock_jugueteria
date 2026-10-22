"""
Fixtures compartidas de los tests de Movimientos.

Ninguno necesita Productos, Redis ni RabbitMQ corriendo: Productos se simula
con `ProductosFalso`, que reproduce lo que importa de su comportamiento real
(el ajuste de stock es idempotente por clave), y RabbitMQ con un publicador
falso que registra lo que se le pide publicar.
"""
import os
import tempfile
import threading
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# app.core.jwt_auth exige AUTH_SECRET_KEY al importarse. Valor de prueba (no es
# un secreto real); si el entorno ya la define (CI), se respeta.
os.environ.setdefault("AUTH_SECRET_KEY", "clave-de-test-no-usar-en-produccion")
# Los tests no arrancan el hilo que publica en RabbitMQ.
os.environ["OUTBOX_RELAY_HABILITADO"] = "false"

from app.clients.productos_client import ProductosClientError  # noqa: E402
from app.database.db import Base  # noqa: E402
from app.models import movimiento as _m, outbox as _o  # noqa: E402,F401  (registran las tablas en Base)

TOKEN_FALSO = "token-de-prueba-no-valido"


@pytest.fixture()
def SessionLocal():
    """Sesiones sobre un archivo SQLite temporal (cada sesión abre su propia conexión)."""
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


class ProductosFalso:
    """
    Doble de prueba del servicio de Productos, con su semántica real:

    - ajustar_stock aplica el cambio UNA vez por `clave` (reintentos con la
      misma clave no vuelven a modificar el stock);
    - 404 si el producto no existe, 409 si no alcanza el stock;
    - se puede "romper" (`caido`) para simular timeouts, o hacer que el ajuste
      se APLIQUE pero la respuesta se pierda (`perder_respuesta`), que es el
      caso incierto más difícil.

    Cada método es seguro para usar desde varios hilos.
    """

    def __init__(self, stock_inicial=None):
        self.stock = dict(stock_inicial or {1: 10})
        self.claves_aplicadas = {}  # clave -> (id_producto, delta)
        self.llamadas_ajustar = []  # (id_producto, delta, clave)
        self.caido = False
        self.perder_respuesta = False
        self.error_http = None  # p. ej. ProductosClientError(401, ...)
        self._lock = threading.Lock()

    def _snapshot(self, id_producto):
        return {
            "id_producto": id_producto,
            "nombre": f"Producto {id_producto}",
            "stock_actual": self.stock[id_producto],
            "stock_minimo": 3,
        }

    def ajustar_stock(self, id_producto, delta, token, clave=None):
        with self._lock:
            self.llamadas_ajustar.append((id_producto, delta, clave))
            if self.caido:
                raise ProductosClientError(503, "No se pudo conectar con el servicio de Productos para ajustar el stock.")
            if self.error_http is not None:
                raise self.error_http
            if clave in self.claves_aplicadas:  # reintento: no se vuelve a aplicar
                return self._snapshot(id_producto)
            if id_producto not in self.stock:
                raise ProductosClientError(404, f"No existe el producto con id_producto={id_producto}.")
            if delta < 0 and self.stock[id_producto] < -delta:
                raise ProductosClientError(409, "Stock insuficiente para registrar este movimiento.")
            self.stock[id_producto] += delta
            if clave:
                self.claves_aplicadas[clave] = (id_producto, delta)
            if self.perder_respuesta:
                raise ProductosClientError(503, "El servicio de Productos no respondió a tiempo al ajustar el stock.")
            return self._snapshot(id_producto)

    def consultar_ajuste(self, clave, token):
        with self._lock:
            if self.caido:
                raise ProductosClientError(503, "No se pudo conectar con el servicio de Productos.")
            if clave in self.claves_aplicadas:
                id_producto, delta = self.claves_aplicadas[clave]
                return {"clave": clave, "id_producto": id_producto, "delta": delta}
            return None

    def obtener_producto(self, id_producto, token):
        with self._lock:
            if self.caido:
                raise ProductosClientError(503, "No se pudo conectar con el servicio de Productos.")
            return self._snapshot(id_producto)


@pytest.fixture()
def productos(monkeypatch):
    """Reemplaza el cliente HTTP de Productos por el doble de prueba."""
    from app.clients import productos_client

    falso = ProductosFalso()
    monkeypatch.setattr(productos_client, "ajustar_stock", falso.ajustar_stock)
    monkeypatch.setattr(productos_client, "consultar_ajuste", falso.consultar_ajuste)
    monkeypatch.setattr(productos_client, "obtener_producto", falso.obtener_producto)
    return falso


class PublicadorFalso:
    """Doble de prueba de RabbitMQ para el relay/outbox."""

    def __init__(self):
        self.publicados = []  # (routing_key, payload)
        self.fallar_siempre = False
        self.fallar_en = set()  # índices (0-based) de la secuencia de publicaciones que fallan
        self.aperturas = 0
        self._intentos = 0

    @contextmanager
    def abrir(self):
        self.aperturas += 1
        if self.fallar_siempre:
            raise ConnectionError("RabbitMQ caído")

        def publicar(routing_key, payload):
            indice = self._intentos
            self._intentos += 1
            if indice in self.fallar_en:
                raise ConnectionError("el broker no confirmó el mensaje")
            self.publicados.append((routing_key, payload))

        yield publicar


@pytest.fixture()
def rabbit():
    return PublicadorFalso()


# ---------------------------------------------------------------------------
# Fixtures de tests/test_bateria_minima.py (tests HTTP con la autenticación
# simulada). Se apoyan en las de arriba: la base temporal (SessionLocal) y el
# doble de Productos (ProductosFalso).
# ---------------------------------------------------------------------------
PAYLOAD_ADMIN_FALSO = {
    "sub": "1",
    "email": "admin@test.com",
    "rol": "admin",
    "tipo": "access",
    "jti": "jti-fake-admin",
}


@pytest.fixture()
def session_factory(SessionLocal):
    """Fábrica de sesiones sobre la base SQLite temporal (alias de SessionLocal)."""
    return SessionLocal


@pytest.fixture()
def productos_mock(monkeypatch):
    """
    Productos acepta los ajustes (con stock de sobra). Devuelve un mock que
    envuelve al doble `ProductosFalso`, para poder contar o inspeccionar las
    llamadas (`call_count`, `assert_not_called()`).
    """
    from unittest.mock import MagicMock

    from app.clients import productos_client

    falso = ProductosFalso(stock_inicial={1: 100_000})
    mock = MagicMock(wraps=falso.ajustar_stock)
    monkeypatch.setattr(productos_client, "ajustar_stock", mock)
    return mock


@pytest.fixture()
def client(SessionLocal, productos_mock, monkeypatch):
    """
    TestClient de la app real, con una sesión de base NUEVA por request (como
    en producción) y la autenticación simulada como admin (sin JWT reales ni
    consulta de revocación).
    """
    from fastapi.testclient import TestClient

    from app import main as main_module
    from app.core.jwt_auth import obtener_token_y_payload
    from app.database.db import get_db
    from app.main import app

    # No tocar la base real al arrancar.
    monkeypatch.setattr(main_module, "inicializar_base", lambda: None)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[obtener_token_y_payload] = lambda: (TOKEN_FALSO, PAYLOAD_ADMIN_FALSO)

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
