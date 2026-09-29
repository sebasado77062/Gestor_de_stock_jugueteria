"""
Schemas de Producto.
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional


class ProductoBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=120, description="Nombre del producto")
    marca: Optional[str] = Field(None, max_length=80)
    precio_venta: float = Field(..., ge=0, description="Precio de venta, no puede ser negativo")
    stock_minimo: int = Field(..., ge=0, description="Stock mínimo, no puede ser negativo")
    categoria: Optional[str] = Field(None, max_length=60)


class ProductoCreate(ProductoBase):
    """
    Schema usado en el POST /api/v1/productos (creación).

    A diferencia de ProductoUpdate, sí acepta stock_actual: al dar de alta
    un producto por primera vez hace falta declarar con cuánto stock inicial
    entra al inventario. Es opcional (default 0) para permitir cargar la
    ficha del producto y sumar stock después mediante un movimiento de
    entrada, una vez que exista services/movimientos.
    """
    stock_actual: int = Field(0, ge=0, description="Stock inicial al crear el producto")


class ProductoUpdate(ProductoBase):
    """
    Schema usado en el PUT /api/v1/productos/{id} (actualización).

    Deliberadamente NO incluye stock_actual. El stock operativo se modifica
    únicamente a través de movimientos de inventario (entradas, salidas,
    ajustes), nunca editando la ficha del producto. Esto evita que una
    edición de datos descriptivos (nombre, precio, marca, categoría) pise
    accidentalmente el stock vigente con un valor desactualizado que el
    cliente tenía cargado en un formulario.

    Ver services/productos/README.md, sección "Decisiones de diseño",
    para el detalle de este cambio (issue #6).
    """
    pass


class ProductoOut(ProductoBase):
    """Schema de salida: lo que la API devuelve al cliente."""
    id_producto: int
    stock_actual: int
    activo: bool

    model_config = ConfigDict(from_attributes=True)


class AjusteStockIn(BaseModel):
    """
    Schema de entrada para PATCH /api/v1/productos/{id}/ajustar-stock.

    delta positivo -> incrementa el stock (ingreso, devolución).
    delta negativo -> intenta decrementar el stock (venta, estropeo); si no
    hay stock suficiente, el ajuste se rechaza de forma atómica.
    """
    delta: int = Field(
        ...,
        description="Cantidad a sumar (positivo) o restar (negativo) del stock_actual",
    )