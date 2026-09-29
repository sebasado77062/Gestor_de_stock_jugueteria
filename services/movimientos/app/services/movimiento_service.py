"""
Capa de Servicios/Controladores para Movimiento.

Orquesta el registro de un movimiento: primero ajusta el stock en el
servicio de Productos (que es la fuente de verdad del stock) y, solo si
ese ajuste fue aceptado, persiste el movimiento como registro histórico.

Autenticación: todas las funciones que hablan con Productos reciben el
token JWT del usuario original y lo propagan al cliente HTTP. Movimientos
no emite ni valida roles por su cuenta más allá de exigir que haya un
usuario autenticado (ver app/core/jwt_auth.py).
"""
from sqlalchemy.orm import Session

from app.clients import productos_client
from app.models.movimiento import Movimiento
from app.schemas.movimiento import MovimientoCreate

# Categorías que descuentan stock vs. las que lo incrementan.
CATEGORIAS_QUE_DESCUENTAN = {"venta", "estropeo"}
CATEGORIAS_QUE_INCREMENTAN = {"ingreso", "devolucion"}


def _calcular_delta(categoria: str, cantidad: int) -> int:
    if categoria in CATEGORIAS_QUE_DESCUENTAN:
        return -cantidad
    return cantidad


def registrar_movimiento(db: Session, datos: MovimientoCreate, token: str) -> Movimiento:
    """
    Registra un movimiento de stock.

    1) Ajusta el stock en Productos de forma atómica, reenviando el token
       del usuario (puede levantar ProductosClientError: 401 si venció la
       sesión, 403 si el rol no alcanza, 404 si no existe el producto, 409
       si no hay stock suficiente, 503/502 si Productos no responde).
    2) Solo si ese ajuste fue aceptado, se persiste el movimiento.
    """
    delta = _calcular_delta(datos.categoria_movimiento, datos.cantidad_movida)

    productos_client.ajustar_stock(datos.id_producto, delta, token)

    nuevo = Movimiento(
        categoria_movimiento=datos.categoria_movimiento,
        cantidad_movida=datos.cantidad_movida,
        id_producto=datos.id_producto,
        dni_cliente=datos.dni_cliente,
        dni_empleado=datos.dni_empleado,
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo


def obtener_producto(id_producto: int, token: str) -> dict:
    """
    Consulta el producto en el servicio de Productos (proxy autenticado).
    Usado por el router para validar que el producto exista antes de
    mostrarlo o antes de aceptar ciertas operaciones.
    """
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