"""
Capa de Rutas para la entidad Movimiento.

Todos los endpoints exigen un usuario autenticado (JWT válido). El token
del usuario se propaga al servicio de Productos al ajustar el stock, para
que sea Productos quien aplique sus propias reglas de rol sobre ese usuario
(no se usan credenciales de servicio).

Consistencia (ver services/movimiento_service.py): registrar un movimiento es
una saga de tres pasos (guardar la intención -> ajustar stock en Productos ->
confirmar). Si Productos no responde, el movimiento queda `pendiente` y se
completa cuando el cliente reintenta con la misma Idempotency-Key.

Idempotencia: POST /movimientos acepta un header opcional Idempotency-Key. La
clave se guarda en la base de datos con un índice único, por lo que el
"mismo pedido" nunca crea dos movimientos ni ajusta el stock dos veces, ni
siquiera con dos pedidos simultáneos.

Mensajería: al CONFIRMAR el movimiento se guarda el evento movimiento.registrado
en el outbox (misma transacción); un relay lo publica en RabbitMQ (exchange
stock.events). Una falla de RabbitMQ NO afecta la respuesta ni pierde el
evento: queda pendiente y se reintenta.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.clients.productos_client import ProductosClientError
from app.core.jwt_auth import obtener_token_y_payload, requerir_rol
from app.database.db import get_db
from app.messaging import outbox
from app.schemas.movimiento import (
    CantidadMovimientosOut,
    MovimientoCreate,
    MovimientoOut,
    ReconciliacionOut,
)
from app.services import movimiento_service
from app.services.movimiento_service import ERRORES_DEFINITIVOS, IdempotencyKeyReutilizada

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Movimientos"])


@router.post("/movimientos", response_model=MovimientoOut, status_code=status.HTTP_201_CREATED)
def crear_movimiento(
    movimiento: MovimientoCreate,
    db: Session = Depends(get_db),
    credenciales: tuple[str, dict] = Depends(obtener_token_y_payload),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key", max_length=100),
):
    """
    POST /api/v1/movimientos -> 201 Created.

    Requiere usuario autenticado (cualquier rol). El token se reenvía a
    Productos para que aplique sus reglas de rol (admin o empleado).

    Si se envía el header Idempotency-Key y ya existe un movimiento con esa
    clave, se devuelve ese movimiento sin volver a ajustar el stock (si estaba
    pendiente, se completa ahora). Reutilizar la clave con un contenido
    distinto devuelve 422.

    Errores: 404 (producto inexistente) y 409 (stock insuficiente) son
    definitivos y no dejan nada registrado. 401/403/502/503 dejan el movimiento
    `pendiente`: el mensaje lo aclara y reintentar con la misma clave lo completa.
    """
    token, _ = credenciales

    try:
        nuevo = movimiento_service.registrar_movimiento(db, movimiento, token, idempotency_key)
    except IdempotencyKeyReutilizada as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    except ProductosClientError as error:
        detalle = error.mensaje
        if error.status_code not in ERRORES_DEFINITIVOS:
            detalle += (
                " El movimiento quedó pendiente de confirmación: reintentá y no se va a duplicar "
                "(si usás la API directamente, repetí el pedido con la misma Idempotency-Key)."
            )
        raise HTTPException(status_code=error.status_code, detail=detalle)

    # El evento ya está guardado en el outbox (misma transacción que la
    # confirmación). Se avisa al relay para que lo publique enseguida; si
    # RabbitMQ no está, el evento espera en el outbox y se reintenta.
    outbox.despertar_relay()
    return nuevo


@router.post(
    "/movimientos/reconciliar",
    response_model=ReconciliacionOut,
    status_code=status.HTTP_200_OK,
)
def reconciliar_movimientos_pendientes(
    antiguedad_minima_segundos: int = Query(
        300,
        ge=0,
        description="Solo se revisan los pendientes con al menos esta antigüedad (evita pisar un reintento en curso).",
    ),
    db: Session = Depends(get_db),
    credenciales: tuple[str, dict] = Depends(requerir_rol("admin")),
):
    """
    POST /api/v1/movimientos/reconciliar -> 200 OK. Solo admin.

    Resuelve los movimientos que quedaron `pendiente` (porque Productos no
    respondió, o falló el guardado final) consultando a Productos si el ajuste
    de stock llegó a aplicarse: si se aplicó, el movimiento se confirma; si no,
    se descarta. NUNCA vuelve a modificar el stock. Los que no se pueden
    consultar quedan pendientes para otra pasada.
    """
    token, _ = credenciales
    resumen = movimiento_service.reconciliar_pendientes(db, token, antiguedad_minima_segundos)
    if resumen["confirmados"]:
        outbox.despertar_relay()
    return resumen


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


@router.get(
    "/productos/{id_producto}/movimientos/cantidad",
    response_model=CantidadMovimientosOut,
    status_code=status.HTTP_200_OK,
)
def contar_movimientos_de_producto(
    id_producto: int,
    db: Session = Depends(get_db),
    _credenciales: tuple[str, dict] = Depends(obtener_token_y_payload),
):
    """
    GET /api/v1/productos/{id}/movimientos/cantidad -> 200 OK.

    Cantidad de movimientos (confirmados o pendientes) de un producto. Lo
    consulta el servicio de Productos antes de eliminar un producto, para
    decidir entre borrado físico y baja lógica sin descargar todo el historial.
    """
    return {
        "id_producto": id_producto,
        "cantidad": movimiento_service.contar_movimientos_por_producto(db, id_producto),
    }
