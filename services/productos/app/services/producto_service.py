"""
Capa de Servicios/Controladores para Producto.

Contiene la lógica de negocio: acceso a la base de datos a través del
modelo, y las reglas propias del dominio (por ejemplo, verificar que el
producto exista antes de actualizarlo o eliminarlo). Las rutas (routers)
no acceden directamente a la base de datos: siempre pasan por aquí.
"""
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients import movimientos_client
from app.models.ajuste_stock import AjusteStock
from app.models.producto import Producto
from app.schemas.producto import ProductoCreate, ProductoUpdate

# Resultado de ajustar_stock_atomico cuando una Idempotency-Key ya fue usada
# para un ajuste DISTINTO (otro producto u otra cantidad): es un uso incorrecto
# de la clave, no un reintento.
CLAVE_REUTILIZADA = "clave_reutilizada"


def obtener_productos(db: Session, incluir_inactivos: bool = False):
    """
    RF02: Listar todos los productos registrados en el inventario.

    Por defecto excluye los productos dados de baja (activo=False), ya que
    conceptualmente ya no forman parte del catálogo operativo. Se pueden
    incluir explícitamente con incluir_inactivos=True (por ejemplo, para una
    vista de administración o auditoría).
    """
    query = db.query(Producto)
    if not incluir_inactivos:
        query = query.filter(Producto.activo.is_(True))
    return query.all()


def obtener_producto_por_id(db: Session, id_producto: int, incluir_inactivos: bool = True):
    """
    RF03: Consultar un producto específico a partir de su ID_Producto.

    A diferencia del listado, por defecto SÍ incluye inactivos: acceder a un
    producto por ID puntual (para editarlo, verlo en un detalle, o para que
    movimientos valide que existe) debe seguir funcionando aunque esté dado
    de baja. Quien necesite excluir inactivos explícitamente puede pasar
    incluir_inactivos=False.
    """
    query = db.query(Producto).filter(Producto.id_producto == id_producto)
    if not incluir_inactivos:
        query = query.filter(Producto.activo.is_(True))
    return query.first()


def crear_producto(db: Session, producto: ProductoCreate):
    """RF01: Crear un nuevo producto."""
    nuevo_producto = Producto(**producto.model_dump())
    db.add(nuevo_producto)
    db.commit()
    db.refresh(nuevo_producto)
    return nuevo_producto


def crear_productos_masivo(db: Session, productos: list[ProductoCreate]):
    """
    Inserta varios productos en un único commit (usado por el endpoint de
    seed de datos de prueba, ver app/routers/seed_router.py). Evitar un
    commit por producto es más eficiente y, si algo falla a mitad de la
    carga, no deja registros parciales confirmados.
    """
    nuevos = [Producto(**p.model_dump()) for p in productos]
    db.add_all(nuevos)
    db.commit()
    for producto_db in nuevos:
        db.refresh(producto_db)
    return nuevos


def actualizar_producto(db: Session, id_producto: int, datos: ProductoUpdate):
    """
    RF04: Modificar los datos descriptivos de un producto existente.

    IMPORTANTE (issue #6): datos es un ProductoUpdate, que no incluye
    stock_actual. Por lo tanto este método nunca toca ni pisa el stock
    vigente del producto, sin importar qué campos se envíen: solo actualiza
    nombre, marca, precio_venta, stock_minimo y categoria. El stock se
    modifica exclusivamente a través de movimientos de inventario.
    """
    producto_db = obtener_producto_por_id(db, id_producto)
    if not producto_db:
        return None
    for campo, valor in datos.model_dump().items():
        setattr(producto_db, campo, valor)
    db.commit()
    db.refresh(producto_db)
    return producto_db


def tiene_movimientos_asociados(db: Session, id_producto: int, token: Optional[str] = None) -> bool:
    """
    Indica si el producto tiene movimientos de stock registrados, consultando
    al servicio de Movimientos por su API REST (ver
    app/clients/movimientos_client.py). Productos no accede a la base de
    Movimientos: cada servicio es dueño de sus datos.

    `token` es el JWT del usuario que pidió la operación, reenviado a
    Movimientos (no se usan credenciales de servicio).

    Cuenta los movimientos confirmados y también los pendientes de
    confirmación (el stock pudo haberse ajustado ya). Si Movimientos no
    responde se levanta MovimientosClientError: la consulta FALLA CERRADA, es
    decir, no se supone "no tiene movimientos" (eso llevaría a borrar
    físicamente un producto con historial).
    """
    return movimientos_client.contar_movimientos(id_producto, token) > 0


def eliminar_producto(db: Session, id_producto: int, token: Optional[str] = None):
    """
    RF05: Eliminar (dar de baja) un producto del inventario.

    Comportamiento (issue #6):
    - Si el producto NO tiene movimientos asociados: se borra físicamente
      de la base de datos, como antes.
    - Si el producto TIENE movimientos asociados: no se borra el registro
      (se perdería la trazabilidad histórica de esos movimientos), sino que
      se marca activo=False (soft delete / baja lógica). El producto deja
      de aparecer en el listado por defecto (GET /productos) pero sigue
      siendo consultable por ID y conserva su historial.

    Devuelve el objeto Producto afectado (borrado o dado de baja), o None
    si no existía. El llamador puede inspeccionar producto.activo para
    saber cuál de los dos casos ocurrió.

    Si no se puede consultar a Movimientos se levanta MovimientosClientError
    y NO se borra ni se da de baja nada (ver tiene_movimientos_asociados).

    Límite conocido: la consulta a Movimientos y el borrado no forman una
    única transacción (son servicios distintos). Un movimiento registrado
    justo entre ambos pasos no se vería; la ventana es de milisegundos.
    """
    producto_db = obtener_producto_por_id(db, id_producto)
    if not producto_db:
        return None

    if tiene_movimientos_asociados(db, id_producto, token):
        producto_db.activo = False
        db.commit()
        db.refresh(producto_db)
        return producto_db

    db.delete(producto_db)
    db.commit()
    return producto_db


def obtener_ajuste_por_clave(db: Session, clave: str) -> Optional[AjusteStock]:
    """Devuelve el ajuste aplicado con esa Idempotency-Key, o None si nunca se aplicó."""
    return db.get(AjusteStock, clave)


def _resultado_de_clave_ya_usada(previo: AjusteStock, id_producto: int, delta: int) -> str:
    """Una clave ya registrada: es un reintento legítimo solo si el ajuste es el mismo."""
    if previo.id_producto != id_producto or previo.delta != delta:
        return CLAVE_REUTILIZADA
    return "ok"


def ajustar_stock_atomico(db: Session, id_producto: int, delta: int, clave: Optional[str] = None):
    """
    Ajusta el stock de un producto de forma ATÓMICA a nivel de base de datos,
    evitando la condición de carrera clásica de "leer -> validar en Python ->
    escribir" (lost update / sobreventa) cuando llegan ventas concurrentes.

    delta > 0 -> incrementa el stock (ingreso, devolución). Siempre se aplica.
    delta < 0 -> intenta decrementar |delta| unidades (venta, estropeo). Solo
                 se aplica si stock_actual >= |delta|.

    Idempotencia (opcional): si se pasa `clave`, el cambio de stock y el
    registro de la clave (tabla ajustes_stock) se confirman en la MISMA
    transacción. Si la clave ya había sido aplicada, el ajuste NO se repite y
    se devuelve "ok" (reintento). Si dos pedidos con la misma clave llegan a
    la vez, la clave primaria deja pasar a uno solo: el otro revierte su
    cambio y también recibe "ok". Un ajuste rechazado por falta de stock no
    registra la clave, así que se puede volver a intentar más tarde.

    Devuelve:
        "ok"                si el ajuste se aplicó (o ya se había aplicado con esa clave).
        "sin_stock"         si no había stock suficiente (solo puede pasar con delta < 0).
        CLAVE_REUTILIZADA   si la clave ya se usó para un ajuste distinto.
        None                si el producto no existe.
    """
    if clave:
        previo = db.get(AjusteStock, clave)
        if previo is not None:
            return _resultado_de_clave_ya_usada(previo, id_producto, delta)

    producto = obtener_producto_por_id(db, id_producto)
    if not producto:
        return None

    if delta >= 0:
        sentencia = (
            update(Producto)
            .where(Producto.id_producto == id_producto)
            .values(stock_actual=Producto.stock_actual + delta)
        )
    else:
        sentencia = (
            update(Producto)
            .where(Producto.id_producto == id_producto)
            .where(Producto.stock_actual >= -delta)
            .values(stock_actual=Producto.stock_actual + delta)
        )

    resultado = db.execute(sentencia)

    if resultado.rowcount == 0:
        db.rollback()
        return "sin_stock"

    if clave:
        db.add(AjusteStock(clave=clave, id_producto=id_producto, delta=delta))

    try:
        db.commit()
    except IntegrityError:
        # Otro pedido con la misma clave se confirmó primero: este cambio se
        # revierte completo (stock incluido) y se trata como reintento.
        db.rollback()
        previo = db.get(AjusteStock, clave) if clave else None
        if previo is None:
            raise
        return _resultado_de_clave_ya_usada(previo, id_producto, delta)

    return "ok"
