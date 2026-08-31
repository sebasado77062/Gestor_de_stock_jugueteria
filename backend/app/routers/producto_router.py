"""
Capa de Rutas para la entidad Producto.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.database.db import get_db
from app.schemas.producto import ProductoCreate, ProductoUpdate, ProductoOut
from app.services import producto_service

router = APIRouter(prefix="/api/v1/productos", tags=["Productos"])


@router.get("", response_model=List[ProductoOut], status_code=status.HTTP_200_OK)
def listar_productos(db: Session = Depends(get_db)):
    """GET /api/v1/productos -> 200 OK con el listado completo."""
    return producto_service.obtener_productos(db)


@router.get("/{id_producto}", response_model=ProductoOut, status_code=status.HTTP_200_OK)
def obtener_producto(id_producto: int, db: Session = Depends(get_db)):
    """GET /api/v1/productos/{id} -> 200 OK o 404 Not Found."""
    producto = producto_service.obtener_producto_por_id(db, id_producto)
    if not producto:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró un producto con id_producto={id_producto}.",
        )
    return producto


@router.post("", response_model=ProductoOut, status_code=status.HTTP_201_CREATED)
def crear_producto(producto: ProductoCreate, db: Session = Depends(get_db)):
    """
    POST /api/v1/productos -> 201 Created.
    """
    return producto_service.crear_producto(db, producto)


@router.put("/{id_producto}", response_model=ProductoOut, status_code=status.HTTP_200_OK)
def actualizar_producto(id_producto: int, producto: ProductoUpdate, db: Session = Depends(get_db)):
    """PUT /api/v1/productos/{id} -> 200 OK o 404 Not Found."""
    actualizado = producto_service.actualizar_producto(db, id_producto, producto)
    if not actualizado:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró un producto con id_producto={id_producto}.",
        )
    return actualizado


@router.delete("/{id_producto}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_producto(id_producto: int, db: Session = Depends(get_db)):
    """DELETE /api/v1/productos/{id} -> 204 No Content o 404 Not Found."""
    eliminado = producto_service.eliminar_producto(db, id_producto)
    if not eliminado:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró un producto con id_producto={id_producto}.",
        )
    return None
