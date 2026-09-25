"""
Schemas de Producto.
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional


class ProductoBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=120, description="Nombre del producto")
    marca: Optional[str] = Field(None, max_length=80)
    precio_venta: float = Field(..., ge=0, description="Precio de venta, no puede ser negativo")
    stock_actual: int = Field(..., ge=0, description="Stock actual, no puede ser negativo")
    stock_minimo: int = Field(..., ge=0, description="Stock mínimo, no puede ser negativo")
    categoria: Optional[str] = Field(None, max_length=60)


class ProductoCreate(ProductoBase):
    """Schema usado en el POST /api/v1/productos (creación)."""
    pass


class ProductoUpdate(ProductoBase):
    """Schema usado en el PUT /api/v1/productos/{id} (actualización)."""
    pass


class ProductoOut(ProductoBase):
    """Schema de salida: lo que la API devuelve al cliente."""
    id_producto: int

    model_config = ConfigDict(from_attributes=True)
