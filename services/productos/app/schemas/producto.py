"""
Schemas de Producto.
"""
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional


class ProductoBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=120, description="Nombre del producto")
    marca: Optional[str] = Field(None, max_length=80)
    precio_venta: float = Field(..., ge=0, description="Precio de venta, no puede ser negativo")
    stock_minimo: int = Field(..., ge=0, description="Stock mínimo, no puede ser negativo")
    categoria: Optional[str] = Field(None, max_length=60)


# Caracteres que un texto de producto (nombre, marca, categoría) no puede
# contener, porque son los típicos de una inyección:
#   < >        HTML / script (XSS)
#   ; =        encadenado de SQL / atributos HTML inyectados
#   ` \         comandos y escapes
#   { }        plantillas ({{...}}, ${...})
#   control    byte nulo, saltos de línea (CRLF), tabulaciones
# Se permiten letras con tildes y ñ, números, espacios y la puntuación de uso
# normal en un catálogo (. , - ' & / ( ) + : % # ° ® ™ ! ?).
PATRON_TEXTO_SEGURO = r"^[^<>;=`\\{}\x00-\x1f\x7f]*$"


class ProductoEntrada(ProductoBase):
    """
    Base de los schemas de ENTRADA (alta y edición): suma el filtro de
    caracteres peligrosos a los tres campos de texto libre.

    A propósito NO se aplica en ProductoBase ni en ProductoOut: la salida no
    se filtra, para que un dato viejo ya guardado nunca haga fallar un GET
    con un 500. El filtro actúa solo sobre lo que entra por la API.
    """
    nombre: str = Field(
        ..., min_length=1, max_length=120, pattern=PATRON_TEXTO_SEGURO,
        description="Nombre del producto",
    )
    marca: Optional[str] = Field(None, max_length=80, pattern=PATRON_TEXTO_SEGURO)
    categoria: Optional[str] = Field(None, max_length=60, pattern=PATRON_TEXTO_SEGURO)


class ProductoCreate(ProductoEntrada):
    """
    Schema usado en el POST /api/v1/productos (creación).

    A diferencia de ProductoUpdate, sí acepta stock_actual: al dar de alta
    un producto por primera vez hace falta declarar con cuánto stock inicial
    entra al inventario. Es opcional (default 0) para permitir cargar la
    ficha del producto y sumar stock después mediante un movimiento de
    entrada, una vez que exista services/movimientos.
    """
    stock_actual: int = Field(0, ge=0, description="Stock inicial al crear el producto")


class ProductoUpdate(ProductoEntrada):
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


class AjusteStockOut(BaseModel):
    """
    Registro de un ajuste de stock aplicado con una Idempotency-Key. Lo consulta
    Movimientos para saber si una operación llegó a aplicarse (ver
    GET /api/v1/productos/ajustes/{clave}).
    """
    clave: str
    id_producto: int
    delta: int
    fecha_hora: datetime

    model_config = ConfigDict(from_attributes=True)
