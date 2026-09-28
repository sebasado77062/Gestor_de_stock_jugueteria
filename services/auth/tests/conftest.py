"""
Fixtures compartidas para los tests del servicio auth.

A diferencia de services/productos (que solo depende de SQL), auth también
depende de Redis para blacklist, bloqueo por intentos fallidos, y rotación
de refresh tokens. Los tests usan:

- SQLite en memoria (StaticPool) para la tabla de usuarios, igual que
  productos: aislada por test, no toca el filesystem.
- La base de datos número 15 de Redis (REDIS_URL apunta a .../15) en lugar
  de la 0 por defecto, para no pisar datos de un Redis real que pudiera
  estar corriendo en el mismo host para otro propósito. Se hace
  flushdb() antes y después de cada test para que no haya estado que se
  filtre entre tests (Redis no tiene el equivalente al "engine en memoria
  nuevo por test" de SQLAlchemy).

Requiere un Redis corriendo y alcanzable en REDIS_URL (o localhost:6379/15
por defecto) para poder correr esta suite — no se mockea, porque los tres
mecanismos que el issue #7 pide probar (TTL, bloqueo, rotación con
concurrencia real) son precisamente el comportamiento de Redis en sí.
"""
import os

# Debe fijarse ANTES de importar app.main / app.core.security, porque esos
# módulos leen la variable de entorno al nivel de módulo (al importarse).
os.environ.setdefault("AUTH_SECRET_KEY", "clave-de-test-no-usar-en-produccion")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.database.db import Base, get_db
from app.core.redis_client import redis_client as redis_conn


@pytest.fixture(autouse=True)
def limpiar_redis():
    """
    Se ejecuta automáticamente antes y después de cada test (autouse):
    vacía la base de datos 15 de Redis para que ningún estado (blacklist,
    intentos fallidos, refresh tokens) se filtre de un test a otro.
    """
    redis_conn.flushdb()
    yield
    redis_conn.flushdb()


@pytest.fixture()
def db_session():
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


@pytest.fixture()
def client(db_session, monkeypatch):
    from app.database import db as db_module

    # Evita que el lifespan real (create_all + creación de admin inicial
    # contra la DB real) se ejecute en los tests; cada test arma su propio
    # usuario admin explícitamente cuando lo necesita (ver fixture
    # admin_token), para que el escenario de cada test sea explícito.
    monkeypatch.setattr(db_module.Base.metadata, "create_all", lambda *a, **k: None)

    from app import main as main_module
    monkeypatch.setattr(main_module, "crear_admin_inicial_si_no_existe", lambda: None)

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
def crear_admin(db_session):
    """Crea un usuario admin directamente en la DB de test y devuelve sus credenciales."""
    from app.services.auth_service import crear_usuario
    from app.schemas.usuario import UsuarioCreate
    from app.models.usuario import RolUsuario

    datos = UsuarioCreate(email="admin@test.com", password="admin12345", nombre="Admin Test", rol=RolUsuario.ADMIN)
    crear_usuario(db_session, datos)
    return {"email": "admin@test.com", "password": "admin12345"}


@pytest.fixture()
def crear_empleado(db_session):
    """Crea un usuario empleado directamente en la DB de test y devuelve sus credenciales."""
    from app.services.auth_service import crear_usuario
    from app.schemas.usuario import UsuarioCreate
    from app.models.usuario import RolUsuario

    datos = UsuarioCreate(email="empleado@test.com", password="empleado123", nombre="Empleado Test", rol=RolUsuario.EMPLEADO)
    crear_usuario(db_session, datos)
    return {"email": "empleado@test.com", "password": "empleado123"}


@pytest.fixture()
def admin_token(client, crear_admin):
    """Loguea al admin de prueba y devuelve el par de tokens (access, refresh)."""
    resp = client.post("/api/v1/auth/login", json=crear_admin)
    assert resp.status_code == 200
    return resp.json()
