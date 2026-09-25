"""
Capa de Servicios/Controladores para Producto.

Contiene la lógica de negocio: acceso a la base de datos a través del
modelo, y las reglas propias del dominio (por ejemplo, verificar que el
producto exista antes de actualizarlo o eliminarlo). Las rutas (routers)
no acceden directamente a la base de datos: siempre pasan por aquí.
"""
from sqlalchemy.orm import Session
from app.models.producto import Producto
from app.schemas.producto import ProductoCreate, ProductoUpdate


def obtener_productos(db: Session):
    """RF02: Listar todos los productos registrados en el inventario."""
    return db.query(Producto).all()


def obtener_producto_por_id(db: Session, id_producto: int):
    """RF03: Consultar un producto específico a partir de su ID_Producto."""
    return db.query(Producto).filter(Producto.id_producto == id_producto).first()


def crear_producto(db: Session, producto: ProductoCreate):
    """RF01: Crear un nuevo producto."""
    nuevo_producto = Producto(**producto.model_dump())
    db.add(nuevo_producto)
    db.commit()
    db.refresh(nuevo_producto)
    return nuevo_producto


def actualizar_producto(db: Session, id_producto: int, datos: ProductoUpdate):
    """RF04: Modificar los datos de un producto existente."""
    producto_db = obtener_producto_por_id(db, id_producto)
    if not producto_db:
        return None
    for campo, valor in datos.model_dump().items():
        setattr(producto_db, campo, valor)
    db.commit()
    db.refresh(producto_db)
    return producto_db


def eliminar_producto(db: Session, id_producto: int):
    """RF05: Eliminar un producto del inventario."""
    producto_db = obtener_producto_por_id(db, id_producto)
    if not producto_db:
        return None
    db.delete(producto_db)
    db.commit()
    return producto_db
