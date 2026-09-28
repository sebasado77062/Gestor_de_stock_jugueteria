"""
Capa de Rutas para la entidad Producto.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session
from typing import List

from app.database.db import get_db
from app.schemas.producto import ProductoCreate, ProductoUpdate, ProductoOut
from app.services import producto_service
from app.core.jwt_auth import requerir_rol

router = APIRouter(prefix="/api/v1/productos", tags=["Productos"])


@router.get(
    "",
    response_model=List[ProductoOut],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(requerir_rol("admin", "empleado"))],
)
def listar_productos(
    incluir_inactivos: bool = Query(
        False,
        description="Si es true, incluye también los productos dados de baja (activo=false).",
    ),
    db: Session = Depends(get_db),
):
    """
    GET /api/v1/productos -> 200 OK con el listado.

    Por defecto solo devuelve productos activos. Usar
    ?incluir_inactivos=true para ver también los dados de baja.

    Requiere autenticación (admin o empleado). Ver services/auth para el
    login y app/core/jwt_auth.py para el mecanismo de validación del JWT.
    """
    return producto_service.obtener_productos(db, incluir_inactivos=incluir_inactivos)


@router.get(
    "/{id_producto}",
    response_model=ProductoOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(requerir_rol("admin", "empleado"))],
)
def obtener_producto(id_producto: int, db: Session = Depends(get_db)):
    """
    GET /api/v1/productos/{id} -> 200 OK o 404 Not Found.

    Devuelve el producto exista o no esté activo (para permitir, por
    ejemplo, ver el detalle de un producto dado de baja).
    """
    producto = producto_service.obtener_producto_por_id(db, id_producto)
    if not producto:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró un producto con id_producto={id_producto}.",
        )
    return producto


@router.post(
    "",
    response_model=ProductoOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requerir_rol("admin", "empleado"))],
)
def crear_producto(producto: ProductoCreate, db: Session = Depends(get_db)):
    """
    POST /api/v1/productos -> 201 Created.
    """
    return producto_service.crear_producto(db, producto)


@router.put(
    "/{id_producto}",
    response_model=ProductoOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(requerir_rol("admin", "empleado"))],
)
def actualizar_producto(id_producto: int, producto: ProductoUpdate, db: Session = Depends(get_db)):
    """
    PUT /api/v1/productos/{id} -> 200 OK o 404 Not Found.

    Actualiza únicamente los datos descriptivos del producto (nombre, marca,
    precio_venta, stock_minimo, categoria). NO modifica stock_actual bajo
    ninguna circunstancia: el schema ProductoUpdate ni siquiera acepta ese
    campo (ver issue #6). El stock se gestiona mediante movimientos.
    """
    actualizado = producto_service.actualizar_producto(db, id_producto, producto)
    if not actualizado:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró un producto con id_producto={id_producto}.",
        )
    return actualizado


@router.delete(
    "/{id_producto}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requerir_rol("admin"))],
)
def eliminar_producto(id_producto: int, response: Response, db: Session = Depends(get_db)):
    """
    DELETE /api/v1/productos/{id}.

    Solo accesible por admin (issue #7): dar de baja o eliminar un
    producto es una decisión más sensible que crearlo o editarlo, así que
    se restringe más que el resto del CRUD.

    Comportamiento (issue #6):
    - 204 No Content: el producto no tenía movimientos asociados y se
      eliminó físicamente.
    - 200 OK con el producto en el body (activo=false): el producto tenía
      movimientos asociados, así que se dio de baja lógica en lugar de
      eliminarlo, para preservar la trazabilidad del historial.
    - 404 Not Found: no existía un producto con ese id.
    """
    eliminado = producto_service.eliminar_producto(db, id_producto)
    if not eliminado:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró un producto con id_producto={id_producto}.",
        )

    if eliminado.activo is False:
        # Baja lógica: se devuelve 200 con el recurso actualizado en vez de
        # 204, para que el cliente pueda distinguir ambos casos y mostrar
        # un mensaje distinto ("dado de baja" vs "eliminado").
        response.status_code = status.HTTP_200_OK
        return ProductoOut.model_validate(eliminado)

    return None

