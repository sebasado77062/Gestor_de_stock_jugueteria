"""
Capa de Rutas para la entidad Movimiento.

Todos los endpoints exigen un usuario autenticado (JWT válido). El token
del usuario se propaga al servicio de Productos al ajustar el stock, para
que sea Productos quien aplique sus propias reglas de rol sobre ese usuario
(no se usan credenciales de servicio).

NOTA: esta es la versión de la vertical básica (Issue #2). La idempotencia
(Idempotency-Key + Redis) y la publicación del evento movimiento.registrado
en RabbitMQ (Issue #4) se agregan en un commit posterior sobre este mismo
archivo — ver services/movimientos/EVENTOS.md.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.clients.productos_client import ProductosClientError
from app.core.jwt_auth import obtener_token_y_payload
from app.database.db import get_db
from app.schemas.movimiento import MovimientoCreate, MovimientoOut
from app.services import movimiento_service

router = APIRouter(prefix="/api/v1", tags=["Movimientos"])


@router.post("/movimientos", response_model=MovimientoOut, status_code=status.HTTP_201_CREATED)
def crear_movimiento(
    movimiento: MovimientoCreate,
    db: Session = Depends(get_db),
    credenciales: tuple[str, dict] = Depends(obtener_token_y_payload),
):
    """
    POST /api/v1/movimientos -> 201 Created.

    Ajusta el stock en Productos (de forma atómica, ver su endpoint
    PATCH /ajustar-stock) y, solo si ese ajuste fue aceptado, registra el
    movimiento como histórico inmutable.

    Requiere un usuario autenticado (cualquier rol). El token se reenvía a
    Productos para que aplique sus propias reglas (rol admin o empleado).
    """
    token, _ = credenciales
    try:
        return movimiento_service.registrar_movimiento(db, movimiento, token)
    except ProductosClientError as error:
        raise HTTPException(status_code=error.status_code, detail=error.mensaje)


@router.get("/movimientos", response_model=List[MovimientoOut], status_code=status.HTTP_200_OK)
def listar_movimientos(
    db: Session = Depends(get_db),
    _credenciales: tuple[str, dict] = Depends(obtener_token_y_payload),
):
    """GET /api/v1/movimientos -> 200 OK con el listado completo (más reciente primero)."""
    return movimiento_service.listar_movimientos(db)


@router.get(
    "/productos/{id_producto}/movimientos",
    response_model=List[MovimientoOut],
    status_code=status.HTTP_200_OK,
)
def listar_movimientos_de_producto(
    id_producto: int,
    db: Session = Depends(get_db),
    _credenciales: tuple[str, dict] = Depends(obtener_token_y_payload),
):
    """
    GET /api/v1/productos/{id}/movimientos -> 200 OK con los movimientos
    de ese producto.
    """
    return movimiento_service.listar_movimientos_por_producto(db, id_producto)