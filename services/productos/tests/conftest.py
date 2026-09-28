"""
Fixtures compartidas para los tests del servicio productos.

Cada test recibe una base de datos SQLite en memoria completamente nueva
(no se comparte estado entre tests, no se toca el filesystem, no depende
de la variable de entorno DATABASE_URL del contenedor). Esto se logra
sobreescribiendo la dependencia get_db de FastAPI con una sesión que apunta
a un engine propio del test.

Autenticación (issue #7): la fixture `client` autentica automáticamente
como un usuario admin (vía dependency_overrides sobre
obtener_usuario_actual, no generando JWTs reales), para que los 38 tests
de CRUD/validaciones/baja lógica que ya existían sigan probando lo que
prueban sin tener que agregarles headers uno por uno. Los tests específicos
de autenticación (tests/test_autenticacion.py) usan la fixture
`client_sin_auth` para verificar 401/403 con el mecanismo real de
validación de JWT en juego.
"""
import os

os.environ.setdefault("AUTH_SECRET_KEY", "clave-de-test-no-usar-en-produccion")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.main import app
from app.database.db import Base, get_db
from app.core.jwt_auth import obtener_usuario_actual


@pytest.fixture()
def db_session():
    """
    Motor y sesión SQLite en memoria, con las tablas creadas desde cero.
    StaticPool asegura que todas las conexiones de este engine compartan
    la misma base en memoria (por defecto, cada conexión SQLite en memoria
    es una base distinta).
    """
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _payload_admin_fake():
    return {"sub": "1", "email": "admin@test.com", "rol": "admin", "tipo": "access", "jti": "jti-fake-admin"}


def _payload_empleado_fake():
    return {"sub": "2", "email": "empleado@test.com", "rol": "empleado", "tipo": "access", "jti": "jti-fake-empleado"}


@pytest.fixture()
def client(db_session, monkeypatch):
    """
    TestClient de FastAPI con get_db sobreescrito para usar la sesión de
    prueba en memoria, y con la autenticación simulada como un admin (ver
    docstring del módulo). Es la fixture que usa la mayoría de los tests
    existentes, que no están probando autenticación en sí, sino el
    comportamiento de negocio de Producto.
    """
    from app.database import db as db_module

    monkeypatch.setattr(db_module.Base.metadata, "create_all", lambda *a, **k: None)

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[obtener_usuario_actual] = _payload_admin_fake

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def client_empleado(db_session, monkeypatch):
    """Igual que `client`, pero autenticado con rol empleado en vez de admin."""
    from app.database import db as db_module

    monkeypatch.setattr(db_module.Base.metadata, "create_all", lambda *a, **k: None)

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[obtener_usuario_actual] = _payload_empleado_fake

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def client_sin_auth(db_session, monkeypatch):
    """
    TestClient SIN override de autenticación: usa el mecanismo real de
    validación de JWT (app/core/jwt_auth.py). Se usa específicamente en
    tests/test_autenticacion.py para probar 401/403 con tokens reales,
    tokens ausentes, tokens con firma inválida, etc.
    """
    from app.database import db as db_module

    monkeypatch.setattr(db_module.Base.metadata, "create_all", lambda *a, **k: None)

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def producto_payload():
    """Payload válido mínimo para crear un producto en los tests."""
    return {
        "nombre": "Muñeca Bebota",
        "marca": "Playkids",
        "precio_venta": 15999.90,
        "stock_actual": 12,
        "stock_minimo": 5,
        "categoria": "Muñecos",
    }

