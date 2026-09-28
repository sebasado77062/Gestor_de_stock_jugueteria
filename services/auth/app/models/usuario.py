"""
Modelo de la entidad Usuario (servicio auth).
"""
import enum

from sqlalchemy import Column, Integer, String, Boolean, Enum
from app.database.db import Base


class RolUsuario(str, enum.Enum):
    """
    Roles soportados en este proyecto (issue #7): dos roles, admin y
    empleado. admin tiene acceso a todo, incluida la consulta de auditoría;
    empleado puede operar productos/movimientos pero no ver la auditoría ni
    gestionar usuarios.
    """
    ADMIN = "admin"
    EMPLEADO = "empleado"


class Usuario(Base):
    __tablename__ = "usuarios"

    id_usuario = Column(Integer, primary_key=True, index=True, autoincrement=True)
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    nombre = Column(String, nullable=False)
    rol = Column(Enum(RolUsuario), nullable=False, default=RolUsuario.EMPLEADO)
    activo = Column(Boolean, nullable=False, default=True, server_default="1")
