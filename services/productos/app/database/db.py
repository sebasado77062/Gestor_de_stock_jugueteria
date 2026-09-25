"""
Configuración de la base de datos.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# La URL de conexión se toma de una variable de entorno para no hardcodear
# rutas ni credenciales en el repo. Por defecto, SQLite en un volumen local
# (útil para desarrollo sin Docker). En docker-compose se sobreescribe para
# apuntar a /data/jugueteria.db, montado como volumen persistente.
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL", "sqlite:///./data/jugueteria.db"
)

# connect_args solo aplica a SQLite (check_same_thread=False porque FastAPI
# puede usar la conexión desde distintos hilos/tareas asíncronas). Si en el
# futuro se usa Postgres u otro motor, este bloque se ignora.
connect_args = (
    {"check_same_thread": False}
    if SQLALCHEMY_DATABASE_URL.startswith("sqlite")
    else {}
)

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """
    Dependencia de FastAPI: abre una sesión de base de datos por request
    y la cierra automáticamente al finalizar (incluso si hay una excepción).
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
