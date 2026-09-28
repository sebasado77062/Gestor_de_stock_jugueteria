"""
Capa de Servicios/Controladores para Producto.

Contiene la lógica de negocio: acceso a la base de datos a través del
modelo, y las reglas propias del dominio (por ejemplo, verificar que el
producto exista antes de actualizarlo o eliminarlo). Las rutas (routers)
no acceden directamente a la base de datos: siempre pasan por aquí.
"""
from sqlalchemy import update
from sqlalchemy.orm import Session
from app.models.producto import Producto
from app.schemas.producto import ProductoCreate, ProductoUpdate


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


def tiene_movimientos_asociados(db: Session, id_producto: int) -> bool:
    """
    Indica si el producto tiene movimientos de stock registrados.

    Placeholder hasta que exista services/movimientos: hoy siempre devuelve
    False porque no hay ninguna tabla ni servicio de movimientos con quién
    consultar. Se deja esta función como el único punto de integración a
    modificar cuando movimientos exista (ya sea consultando su API REST, o
    una tabla compartida), para no tener que tocar eliminar_producto de
    nuevo en ese momento.
    """
    return False


def eliminar_producto(db: Session, id_producto: int):
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
    """
    producto_db = obtener_producto_por_id(db, id_producto)
    if not producto_db:
        return None

    if tiene_movimientos_asociados(db, id_producto):
        producto_db.activo = False
        db.commit()
        db.refresh(producto_db)
        return producto_db

    db.delete(producto_db)
    db.commit()
    return producto_db


def ajustar_stock_atomico(db: Session, id_producto: int, delta: int):
    """
    Ajusta el stock de un producto de forma ATÓMICA a nivel de base de datos,
    evitando la condición de carrera clásica de "leer -> validar en Python ->
    escribir" (lost update / sobreventa) cuando llegan ventas concurrentes.

    delta > 0 -> incrementa el stock (ingreso, devolución). Siempre se aplica.
    delta < 0 -> intenta decrementar |delta| unidades (venta, estropeo). Solo
                 se aplica si stock_actual >= |delta|.

    Devuelve:
        "ok"        si el ajuste se aplicó.
        "sin_stock" si no había stock suficiente (solo puede pasar con delta < 0).
        None        si el producto no existe.
    """
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
    db.commit()

    if resultado.rowcount == 0:
        return "sin_stock"
    return "ok"