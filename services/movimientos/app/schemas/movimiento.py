"""
Schemas de Movimiento.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CategoriaMovimiento = Literal["venta", "ingreso", "estropeo", "devolucion"]


class MovimientoCreate(BaseModel):
    """Schema usado en el POST /api/v1/movimientos (única forma de crear un movimiento)."""

    categoria_movimiento: CategoriaMovimiento = Field(
        ..., description="venta | ingreso | estropeo | devolucion"
    )
    cantidad_movida: int = Field(
        ..., gt=0, description="Cantidad movida, siempre positiva; el signo lo define la categoría"
    )
    id_producto: int = Field(..., description="ID del producto afectado (servicio de Productos)")
    dni_cliente: Optional[str] = Field(None, max_length=20)
    dni_empleado: Optional[str] = Field(None, max_length=20)


class MovimientoOut(BaseModel):
    """Schema de salida: lo que la API devuelve al cliente."""

    id_movimiento: int
    fecha_hora: datetime
    categoria_movimiento: str
    cantidad_movida: int
    id_producto: int
    dni_cliente: Optional[str] = None
    dni_empleado: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
