"""
Capa de Servicios/Controladores para Movimiento.

Registrar un movimiento toca DOS bases de datos de servicios distintos
(Productos: stock; Movimientos: historial). No existe una transacción que las
abarque, así que la consistencia se logra con una saga de "intención primero":

    1. GUARDAR LA INTENCIÓN: se inserta el movimiento como `pendiente`, con una
       `operacion_id` propia y, si el cliente la mandó, su Idempotency-Key
       (índice único: dos pedidos simultáneos con la misma clave no pueden crear
       dos movimientos). Si esto falla, todavía no pasó nada en Productos.
    2. AJUSTAR EL STOCK en Productos enviando `operacion_id` como
       Idempotency-Key. Productos aplica el cambio UNA sola vez por clave, así
       que repetir este paso es seguro.
    3. CONFIRMAR: en una única transacción local, el movimiento pasa a
       `confirmado` y se guarda su evento en el outbox (ver messaging/outbox.py).

Qué pasa si algo falla en cada punto:

- Productos rechaza de forma definitiva (404 producto inexistente, 409 sin
  stock): el stock no se tocó, así que la intención se descarta.
- Productos no responde / hay un error inesperado: NO se sabe si el stock
  cambió. El movimiento queda `pendiente`. Se completa cuando el cliente
  reintenta con la misma Idempotency-Key (paso 2 es idempotente: o aplica ahora
  o responde que ya estaba aplicado), o cuando un admin reconcilia.
- Falla el guardado del paso 3 (el "segundo paso" de la limitación original):
  el stock ya cambió pero el movimiento sigue `pendiente`. Es el mismo caso:
  el reintento o la reconciliación lo confirman. No hace falta "deshacer" el
  stock (compensar): se avanza hasta terminar (recuperación hacia adelante).

Reconciliar NUNCA vuelve a modificar el stock: solo consulta a Productos si la
operación se aplicó y alinea el registro (confirma si se aplicó, descarta si
no). Así una intención que el usuario abandonó no se ejecuta por sorpresa.

Autenticación: todas las funciones que hablan con Productos reciben el
token JWT del usuario original y lo propagan al cliente HTTP.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients import productos_client
from app.clients.productos_client import ProductosClientError
from app.messaging import outbox, publisher
from app.models.movimiento import ESTADO_CONFIRMADO, ESTADO_PENDIENTE, Movimiento
from app.schemas.movimiento import MovimientoCreate

# Categorías que descuentan stock vs. las que lo incrementan.
CATEGORIAS_QUE_DESCUENTAN = {"venta", "estropeo"}
CATEGORIAS_QUE_INCREMENTAN = {"ingreso", "devolucion"}

# Respuestas de Productos que prueban que el stock NO se modificó por esta
# operación (producto inexistente / stock insuficiente). Cualquier otro error
# deja el resultado incierto y el movimiento queda pendiente. Un 401/403 NO
# es una prueba: puede ser el reintento de una operación ya aplicada.
ERRORES_DEFINITIVOS = {404, 409}


class IdempotencyKeyReutilizada(Exception):
    """La Idempotency-Key ya se usó para un movimiento con otro contenido."""


def _calcular_delta(categoria: str, cantidad: int) -> int:
    if categoria in CATEGORIAS_QUE_DESCUENTAN:
        return -cantidad
    return cantidad


def _delta_de(movimiento: Movimiento) -> int:
    return _calcular_delta(movimiento.categoria_movimiento, movimiento.cantidad_movida)


# ---------------------------------------------------------------------------
# Paso 1: guardar la intención (idempotencia atómica)
# ---------------------------------------------------------------------------

def _buscar_por_clave(db: Session, idempotency_key: str) -> Optional[Movimiento]:
    return db.query(Movimiento).filter(Movimiento.idempotency_key == idempotency_key).first()


def _verificar_mismo_contenido(existente: Movimiento, datos: MovimientoCreate) -> Movimiento:
    """Una clave solo es un reintento legítimo si el pedido es el mismo."""
    mismo = (
        existente.categoria_movimiento == datos.categoria_movimiento
        and existente.cantidad_movida == datos.cantidad_movida
        and existente.id_producto == datos.id_producto
        and existente.dni_cliente == datos.dni_cliente
        and existente.dni_empleado == datos.dni_empleado
    )
    if not mismo:
        raise IdempotencyKeyReutilizada(
            "La Idempotency-Key ya fue usada para un movimiento distinto. "
            "Usá una clave nueva para un movimiento nuevo."
        )
    return existente


def iniciar_movimiento(
    db: Session, datos: MovimientoCreate, idempotency_key: Optional[str] = None
) -> Movimiento:
    """
    Paso 1 de la saga. Devuelve el movimiento a completar: uno recién creado
    como `pendiente`, o el ya existente si la Idempotency-Key se repite
    (confirmado o todavía pendiente).

    La unicidad de la clave la resuelve la base de datos: si dos pedidos con la
    misma clave llegan a la vez, uno inserta y el otro recibe IntegrityError y
    toma el movimiento del primero.
    """
    if idempotency_key:
        existente = _buscar_por_clave(db, idempotency_key)
        if existente is not None:
            return _verificar_mismo_contenido(existente, datos)

    nuevo = Movimiento(
        categoria_movimiento=datos.categoria_movimiento,
        cantidad_movida=datos.cantidad_movida,
        id_producto=datos.id_producto,
        dni_cliente=datos.dni_cliente,
        dni_empleado=datos.dni_empleado,
        estado=ESTADO_PENDIENTE,
        operacion_id=str(uuid.uuid4()),
        idempotency_key=idempotency_key,
    )
    db.add(nuevo)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existente = _buscar_por_clave(db, idempotency_key) if idempotency_key else None
        if existente is None:
            raise
        return _verificar_mismo_contenido(existente, datos)
    db.refresh(nuevo)
    return nuevo


# ---------------------------------------------------------------------------
# Pasos 2 y 3: ajustar el stock y confirmar
# ---------------------------------------------------------------------------

def _confirmar(db: Session, movimiento: Movimiento, producto: dict) -> None:
    """
    Paso 3: pendiente -> confirmado + evento en el outbox, en UNA transacción.

    El UPDATE condicional (solo si sigue pendiente) hace la transición atómica:
    si dos pedidos completan el mismo movimiento a la vez, solo uno la logra y
    solo ese encola el evento (no hay eventos duplicados por la carrera).
    """
    db.refresh(movimiento)  # trae fecha_hora asignada por la base, para el payload
    payload = publisher.construir_payload(movimiento, producto)
    resultado = db.execute(
        update(Movimiento)
        .where(Movimiento.id_movimiento == movimiento.id_movimiento)
        .where(Movimiento.estado == ESTADO_PENDIENTE)
        .values(estado=ESTADO_CONFIRMADO)
    )
    if resultado.rowcount == 1:
        outbox.encolar_evento(db, publisher.ROUTING_KEY, payload)
    db.commit()
    db.refresh(movimiento)


def _descartar(db: Session, movimiento: Movimiento) -> None:
    """Elimina una intención que Productos rechazó: nunca llegó a ser un movimiento."""
    db.execute(
        delete(Movimiento)
        .where(Movimiento.id_movimiento == movimiento.id_movimiento)
        .where(Movimiento.estado == ESTADO_PENDIENTE)
    )
    db.commit()
    if movimiento in db:
        db.expunge(movimiento)


def completar_movimiento(db: Session, movimiento: Movimiento, token: str) -> Movimiento:
    """
    Pasos 2 y 3. Es idempotente: sobre un movimiento ya confirmado no hace nada,
    y sobre uno pendiente repite el ajuste con la misma `operacion_id` (Productos
    lo aplica una sola vez).

    Levanta ProductosClientError si Productos falla. Con 404/409 la intención se
    descarta; con cualquier otro error el movimiento queda pendiente.
    """
    if movimiento.estado == ESTADO_CONFIRMADO:
        return movimiento

    try:
        producto = productos_client.ajustar_stock(
            movimiento.id_producto, _delta_de(movimiento), token, clave=movimiento.operacion_id
        )
    except ProductosClientError as error:
        if error.status_code in ERRORES_DEFINITIVOS:
            _descartar(db, movimiento)
        raise

    _confirmar(db, movimiento, producto)
    return movimiento


def registrar_movimiento(
    db: Session, datos: MovimientoCreate, token: str, idempotency_key: Optional[str] = None
) -> Movimiento:
    """
    Registra un movimiento de stock (ver el docstring del módulo para la saga).

    Puede levantar:
      - IdempotencyKeyReutilizada: la clave ya se usó con otro contenido.
      - ProductosClientError: 401/403 si el token no sirve, 404 si no existe el
        producto, 409 si no hay stock suficiente, 503/502 si Productos no
        responde. Con 404/409 no queda nada registrado; con el resto el
        movimiento queda `pendiente` y se puede reintentar con la misma clave.
    """
    movimiento = iniciar_movimiento(db, datos, idempotency_key)
    return completar_movimiento(db, movimiento, token)


# ---------------------------------------------------------------------------
# Reconciliación de movimientos pendientes (sin modificar stock)
# ---------------------------------------------------------------------------

def _antiguedad_segundos(movimiento: Movimiento) -> float:
    fecha = movimiento.fecha_hora
    if fecha.tzinfo is None:  # SQLite devuelve fechas sin zona; CURRENT_TIMESTAMP es UTC
        fecha = fecha.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fecha).total_seconds()


def reconciliar_pendientes(db: Session, token: str, antiguedad_minima_segundos: int = 300) -> dict:
    """
    Resuelve los movimientos que quedaron `pendiente` hace más de
    `antiguedad_minima_segundos`, consultando a Productos si la operación llegó
    a aplicarse (GET /ajustes/{operacion_id}). NO vuelve a ajustar el stock:

      - se aplicó  -> se confirma el movimiento (y se encola su evento);
      - no se aplicó -> se descarta (era una intención abandonada);
      - no se pudo consultar -> queda pendiente para otra pasada.

    El margen de antigüedad evita pisar un movimiento que justo está siendo
    completado por un reintento del cliente.

    Devuelve un resumen {revisados, confirmados, descartados, sin_resolver}.
    """
    resumen = {"revisados": 0, "confirmados": 0, "descartados": 0, "sin_resolver": 0}
    pendientes = (
        db.query(Movimiento)
        .filter(Movimiento.estado == ESTADO_PENDIENTE)
        .order_by(Movimiento.id_movimiento)
        .all()
    )
    for movimiento in pendientes:
        if _antiguedad_segundos(movimiento) < antiguedad_minima_segundos:
            continue
        resumen["revisados"] += 1
        try:
            if movimiento.operacion_id is None:
                resumen["sin_resolver"] += 1
                continue
            ajuste = productos_client.consultar_ajuste(movimiento.operacion_id, token)
            if ajuste is None:
                _descartar(db, movimiento)
                resumen["descartados"] += 1
                continue
            # Se aplicó: el evento lleva el snapshot ACTUAL del producto (el de
            # aquel instante no se conserva); para la alerta de stock bajo es lo útil.
            producto = productos_client.obtener_producto(movimiento.id_producto, token)
            _confirmar(db, movimiento, producto)
            resumen["confirmados"] += 1
        except ProductosClientError:
            resumen["sin_resolver"] += 1
    return resumen


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------

def obtener_producto(id_producto: int, token: str) -> dict:
    """Consulta el producto en el servicio de Productos (proxy autenticado)."""
    return productos_client.obtener_producto(id_producto, token)


def obtener_movimiento_por_id(db: Session, id_movimiento: int):
    return db.query(Movimiento).filter(Movimiento.id_movimiento == id_movimiento).first()


def listar_movimientos(db: Session):
    return db.query(Movimiento).order_by(Movimiento.fecha_hora.desc()).all()


def listar_movimientos_por_producto(db: Session, id_producto: int):
    return (
        db.query(Movimiento)
        .filter(Movimiento.id_producto == id_producto)
        .order_by(Movimiento.fecha_hora.desc())
        .all()
    )


def contar_movimientos_por_producto(db: Session, id_producto: int) -> int:
    """Cantidad de movimientos (confirmados o pendientes) de un producto."""
    return db.query(Movimiento).filter(Movimiento.id_producto == id_producto).count()
