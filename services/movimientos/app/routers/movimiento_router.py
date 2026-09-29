"""
Capa de Rutas para la entidad Movimiento.

Todos los endpoints exigen un usuario autenticado (JWT válido). El token
del usuario se propaga al servicio de Productos al ajustar el stock, para
que sea Productos quien aplique sus propias reglas de rol sobre ese usuario
(no se usan credenciales de servicio).

Idempotencia (Issue #4): POST /movimientos acepta un header opcional
Idempotency-Key. Si la misma clave ya fue procesada, se devuelve el mismo
movimiento sin volver a ajustar stock.

Mensajería (Issue #4): al registrar un movimiento se publica el evento
movimiento.registrado en RabbitMQ (exchange stock.events). Una falla de
mensajería NO impide responder 201: el movimiento ya quedó persistido.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.clients.productos_client import ProductosClientError
from app.core.jwt_auth import obtener_token_y_payload
from app.database.db import get_db
from app.idempotencia import redis_client
from app.messaging import publisher
from app.schemas.movimiento import MovimientoCreate, MovimientoOut
from app.services import movimiento_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Movimientos"])


@router.post("/movimientos", response_model=MovimientoOut, status_code=status.HTTP_201_CREATED)
def crear_movimiento(
    movimiento: MovimientoCreate,
    db: Session = Depends(get_db),
    credenciales: tuple[str, dict] = Depends(obtener_token_y_payload),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """
    POST /api/v1/movimientos -> 201 Created.

    Requiere usuario autenticado (cualquier rol). El token se reenvía a
    Productos para que aplique sus reglas de rol (admin o empleado).

    Si se envía el header Idempotency-Key y ya existe un movimiento
    registrado con esa misma clave, se devuelve ese movimiento sin volver a
    ajustar el stock ni crear un registro duplicado.
    """
    token, _ = credenciales

    if idempotency_key:
        try:
            id_previo = redis_client.obtener_id_movimiento_previo(idempotency_key)
        except Exception:
            # Redis caído: se degrada la idempotencia en este request puntual
            # pero el servicio sigue funcionando.
            logger.exception("No se pudo consultar Redis para la Idempotency-Key %s", idempotency_key)
            id_previo = None

        if id_previo is not None:
            movimiento_previo = movimiento_service.obtener_movimiento_por_id(db, id_previo)
            if movimiento_previo:
                return movimiento_previo

    try:
        nuevo, producto_actualizado = movimiento_service.registrar_movimiento(db, movimiento, token)
    except ProductosClientError as error:
        raise HTTPException(status_code=error.status_code, detail=error.mensaje)

    if idempotency_key:
        try:
            redis_client.registrar_idempotency_key(idempotency_key, nuevo.id_movimiento)
        except Exception:
            logger.exception("No se pudo registrar la Idempotency-Key %s en Redis", idempotency_key)

    # Publica el evento de negocio. Una falla de mensajería NO debe impedir
    # la respuesta 201: la base de datos ya es la fuente de verdad.
    try:
        publisher.publicar_movimiento_registrado(nuevo, producto_actualizado)
    except Exception:
        logger.exception("No se pudo publicar el evento movimiento.registrado (id_movimiento=%s)", nuevo.id_movimiento)

    return nuevo


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
    """GET /api/v1/productos/{id}/movimientos -> 200 OK con los movimientos de ese producto."""
    return movimiento_service.listar_movimientos_por_producto(db, id_producto)