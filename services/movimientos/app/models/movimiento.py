"""
Modelo de la entidad Movimiento.

Capa de Persistencia. Es un registro histórico de SOLO INSERCIONES: no
existen endpoints de UPDATE ni DELETE sobre Movimiento (ver routers). Si en
el futuro hiciera falta "revertir" un movimiento, se documenta como un
nuevo movimiento de reverso, nunca editando o borrando el original: esto
preserva la trazabilidad exigida por la consigna.
"""
from sqlalchemy import Column, Integer, String, DateTime, func

from app.database.db import Base


class Movimiento(Base):
    __tablename__ = "movimientos"

    id_movimiento = Column(Integer, primary_key=True, index=True, autoincrement=True)
    fecha_hora = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # venta | ingreso | estropeo | devolucion
    categoria_movimiento = Column(String, nullable=False, index=True)
    cantidad_movida = Column(Integer, nullable=False)

    id_producto = Column(Integer, nullable=False, index=True)
    dni_cliente = Column(String, nullable=True)
    dni_empleado = Column(String, nullable=True)
