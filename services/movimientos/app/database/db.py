"""
Configuración de la base de datos del servicio de Movimientos.

Es una base propia e independiente de la de Productos (cada servicio es
dueño de sus datos): Movimientos NUNCA consulta directamente las tablas de
Productos, solo se comunica con él vía HTTP (ver app/clients/productos_client.py).
Mismo patrón que services/productos/app/database/db.py.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL", "sqlite:///./data/movimientos.db"
)

connect_args = (
    {"check_same_thread": False}
    if SQLALCHEMY_DATABASE_URL.startswith("sqlite")
    else {}
)

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependencia de FastAPI: abre una sesión por request y la cierra al finalizar."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
