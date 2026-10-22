"""
Schemas de Movimiento.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CategoriaMovimiento = Literal["venta", "ingreso", "estropeo", "devolucion"]

# El DNI son solo dígitos, hasta 8 (mismo criterio que el frontend, ver
# restringirInputDni en frontend/app.js). Aceptar únicamente ese formato deja
# afuera comillas, signos "<>", ";", "--", "../", bytes nulos y saltos de línea.
PATRON_DNI = r"^[0-9]{1,8}$"


class MovimientoCreate(BaseModel):
    """Schema usado en el POST /api/v1/movimientos (única forma de crear un movimiento)."""

    categoria_movimiento: CategoriaMovimiento = Field(
        ..., description="venta | ingreso | estropeo | devolucion"
    )
    cantidad_movida: int = Field(
        ..., gt=0, description="Cantidad movida, siempre positiva; el signo lo define la categoría"
    )
    id_producto: int = Field(..., description="ID del producto afectado (servicio de Productos)")
    dni_cliente: Optional[str] = Field(None, pattern=PATRON_DNI, description="DNI: solo dígitos, hasta 8")
    dni_empleado: Optional[str] = Field(None, pattern=PATRON_DNI, description="DNI: solo dígitos, hasta 8")


class MovimientoOut(BaseModel):
    """Schema de salida: lo que la API devuelve al cliente."""

    id_movimiento: int
    fecha_hora: datetime
    categoria_movimiento: str
    cantidad_movida: int
    id_producto: int
    dni_cliente: Optional[str] = None
    dni_empleado: Optional[str] = None
    estado: Literal["pendiente", "confirmado"] = Field(
        "confirmado",
        description=(
            "confirmado: el stock ya fue ajustado. pendiente: se guardó la solicitud pero "
            "todavía no se confirmó el ajuste de stock (reintentar con la misma Idempotency-Key)."
        ),
    )

    model_config = ConfigDict(from_attributes=True)


class CantidadMovimientosOut(BaseModel):
    """Cantidad de movimientos de un producto (la usa Productos antes de dar de baja)."""

    id_producto: int
    cantidad: int


class ReconciliacionOut(BaseModel):
    """Resultado de POST /api/v1/movimientos/reconciliar."""

    revisados: int
    confirmados: int
    descartados: int
    sin_resolver: int
