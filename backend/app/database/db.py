"""
Configuración de la base de datos.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Archivo de base de datos SQLite (se crea automáticamente en la carpeta backend/)
SQLALCHEMY_DATABASE_URL = "sqlite:///./jugueteria.db"

# check_same_thread=False es necesario porque FastAPI puede usar la conexión
# desde distintos hilos/tareas asíncronas.
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

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
