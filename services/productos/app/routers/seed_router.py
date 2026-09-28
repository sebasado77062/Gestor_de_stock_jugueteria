"""
Endpoint de utilidad para desarrollo/pruebas: carga masiva de productos
con datos variados generados aleatoriamente.

No forma parte del CRUD de negocio de Producto (por eso vive en un router
aparte de producto_router.py) y está pensado para poblar rápido la base
mientras se prueba el frontend (listado, filtro de stock bajo, edición,
borrado) con un volumen de datos realista.

Controlado por la variable de entorno HABILITAR_SEED_ENDPOINT (default
"true"). Se puede desactivar en un entorno donde no se quiera exponer esta
ruta (por ejemplo, si este código se desplegara en producción real) sin
tocar código, seteando HABILITAR_SEED_ENDPOINT=false.
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.schemas.producto import ProductoOut
from app.services import producto_service
from app.utils.datos_prueba import generar_productos_prueba
from app.core.jwt_auth import requerir_rol

router = APIRouter(prefix="/api/v1/dev", tags=["Desarrollo / Datos de prueba"])

_HABILITADO = os.getenv("HABILITAR_SEED_ENDPOINT", "true").lower() == "true"


@router.post(
    "/seed-productos",
    response_model=list[ProductoOut],
    status_code=status.HTTP_201_CREATED,
    summary="Cargar productos de prueba (solo desarrollo)",
    dependencies=[Depends(requerir_rol("admin", "empleado"))],
)
def seed_productos(
    cantidad: int = Query(20, ge=1, le=200, description="Cantidad de productos a generar (1-200)."),
    db: Session = Depends(get_db),
):
    """
    Genera e inserta `cantidad` productos con nombre, marca, categoría,
    precio y stock aleatorios (ver app/utils/datos_prueba.py). Pensado para
    el botón "Cargar datos de prueba" del frontend.

    Requiere estar autenticado (admin o empleado, mismo criterio que
    POST /productos, ya que generar productos de prueba no es más sensible
    que crear un producto real). Devuelve 403 si
    HABILITAR_SEED_ENDPOINT=false en el entorno, independientemente del rol.
    """
    if not _HABILITADO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El endpoint de datos de prueba está deshabilitado en este entorno.",
        )

    productos_generados = generar_productos_prueba(cantidad)
    return producto_service.crear_productos_masivo(db, productos_generados)
