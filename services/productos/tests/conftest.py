"""
Fixtures compartidas para los tests del servicio productos.

Cada test recibe una base de datos SQLite en memoria completamente nueva
(no se comparte estado entre tests, no se toca el filesystem, no depende
de la variable de entorno DATABASE_URL del contenedor). Esto se logra
sobreescribiendo la dependencia get_db de FastAPI con una sesión que apunta
a un engine propio del test.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.main import app
from app.database.db import Base, get_db


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


@pytest.fixture()
def client(db_session, monkeypatch):
    """
    TestClient de FastAPI con get_db sobreescrito para usar la sesión de
    prueba en memoria, en vez de conectar a la base real del contenedor.

    También neutraliza el evento startup de la app (que normalmente crea
    las tablas en la base real vía Base.metadata.create_all(bind=engine)):
    en los tests las tablas ya las crea la fixture db_session sobre su
    propio engine en memoria, así que ejecutar el startup real sería
    innecesario y, si el filesystem no tiene la carpeta data/, fallaría.
    Se parchea Base.metadata.create_all (no la función crear_tablas, que ya
    quedó fijada dentro del callback registrado por @app.on_event al
    importar el módulo) para que no intente tocar el archivo real.
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
