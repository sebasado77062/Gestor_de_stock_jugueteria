"""
Modelo de la entidad Producto.

Capa de Persistencia.
"""
from sqlalchemy import Column, Integer, String, Float
from app.database.db import Base


class Producto(Base):
    __tablename__ = "productos"

    id_producto = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String, nullable=False, index=True)
    marca = Column(String, nullable=True)
    precio_venta = Column(Float, nullable=False)
    stock_actual = Column(Integer, nullable=False, default=0)
    stock_minimo = Column(Integer, nullable=False, default=0)
    categoria = Column(String, nullable=True)
