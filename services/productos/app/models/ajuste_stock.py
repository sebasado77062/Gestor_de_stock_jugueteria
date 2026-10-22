"""
Modelo AjusteStock: registro de los ajustes de stock aplicados con una clave
de idempotencia.

Cuando `movimientos` (u otro cliente) llama a PATCH /ajustar-stock enviando el
header Idempotency-Key, `productos` guarda acá esa clave JUNTO con el cambio de
stock, en la misma transacción de base de datos. Así, si el mismo pedido se
repite (por un timeout, un reintento o la recuperación de un movimiento
pendiente), el ajuste NO se vuelve a aplicar: la clave ya existe y se responde
como un reintento. La unicidad la garantiza la clave primaria, por lo que es
atómico incluso con dos pedidos simultáneos con la misma clave.
"""
from sqlalchemy import Column, DateTime, Integer, String, func

from app.database.db import Base


class AjusteStock(Base):
    __tablename__ = "ajustes_stock"

    clave = Column(String(100), primary_key=True)
    id_producto = Column(Integer, nullable=False, index=True)
    delta = Column(Integer, nullable=False)
    fecha_hora = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
