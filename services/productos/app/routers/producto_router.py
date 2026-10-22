"""
Capa de Rutas para la entidad Producto.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database.db import get_db
from app.schemas.producto import ProductoCreate, ProductoUpdate, ProductoOut, AjusteStockIn, AjusteStockOut
from app.services import producto_service
from app.clients.movimientos_client import MovimientosClientError
from app.core.jwt_auth import obtener_token_crudo, requerir_rol

router = APIRouter(prefix="/api/v1/productos", tags=["Productos"])


@router.get(
    "",
    response_model=List[ProductoOut],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(requerir_rol("admin", "empleado"))],
)
def listar_productos(
    incluir_inactivos: bool = Query(
        False,
        description="Si es true, incluye también los productos dados de baja (activo=false).",
    ),
    db: Session = Depends(get_db),
):
    """
    GET /api/v1/productos -> 200 OK con el listado.

    Por defecto solo devuelve productos activos. Usar
    ?incluir_inactivos=true para ver también los dados de baja.

    Requiere autenticación (admin o empleado). Ver services/auth para el
    login y app/core/jwt_auth.py para el mecanismo de validación del JWT.
    """
    return producto_service.obtener_productos(db, incluir_inactivos=incluir_inactivos)


@router.get(
    "/ajustes/{clave}",
    response_model=AjusteStockOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(requerir_rol("admin", "empleado"))],
)
def obtener_ajuste(clave: str, db: Session = Depends(get_db)):
    """
    GET /api/v1/productos/ajustes/{clave} -> 200 OK o 404 Not Found.

    Indica si un ajuste de stock con esa Idempotency-Key llegó a aplicarse.
    Lo usa Movimientos para reconciliar un movimiento que quedó "pendiente"
    (por ejemplo, porque se cortó la respuesta de ajustar-stock): 200 significa
    que el stock SÍ se modificó; 404, que nunca se aplicó.
    """
    ajuste = producto_service.obtener_ajuste_por_clave(db, clave)
    if not ajuste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No hay un ajuste de stock aplicado con la clave '{clave}'.",
        )
    return ajuste


@router.get(
    "/{id_producto}",
    response_model=ProductoOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(requerir_rol("admin", "empleado"))],
)
def obtener_producto(id_producto: int, db: Session = Depends(get_db)):
    """
    GET /api/v1/productos/{id} -> 200 OK o 404 Not Found.

    Devuelve el producto exista o no esté activo (para permitir, por
    ejemplo, ver el detalle de un producto dado de baja).
    """
    producto = producto_service.obtener_producto_por_id(db, id_producto)
    if not producto:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró un producto con id_producto={id_producto}.",
        )
    return producto


@router.post(
    "",
    response_model=ProductoOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requerir_rol("admin", "empleado"))],
)
def crear_producto(producto: ProductoCreate, db: Session = Depends(get_db)):
    """
    POST /api/v1/productos -> 201 Created.
    """
    return producto_service.crear_producto(db, producto)


@router.put(
    "/{id_producto}",
    response_model=ProductoOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(requerir_rol("admin", "empleado"))],
)
def actualizar_producto(id_producto: int, producto: ProductoUpdate, db: Session = Depends(get_db)):
    """
    PUT /api/v1/productos/{id} -> 200 OK o 404 Not Found.

    Actualiza únicamente los datos descriptivos del producto (nombre, marca,
    precio_venta, stock_minimo, categoria). NO modifica stock_actual bajo
    ninguna circunstancia: el schema ProductoUpdate ni siquiera acepta ese
    campo (ver issue #6). El stock se gestiona mediante movimientos.
    """
    actualizado = producto_service.actualizar_producto(db, id_producto, producto)
    if not actualizado:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró un producto con id_producto={id_producto}.",
        )
    return actualizado


@router.delete(
    "/{id_producto}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requerir_rol("admin"))],
)
def eliminar_producto(
    id_producto: int,
    response: Response,
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(obtener_token_crudo),
):
    """
    DELETE /api/v1/productos/{id}.

    Solo accesible por admin (issue #7): dar de baja o eliminar un
    producto es una decisión más sensible que crearlo o editarlo, así que
    se restringe más que el resto del CRUD.

    Comportamiento (issue #6):
    - 204 No Content: el producto no tenía movimientos asociados y se
      eliminó físicamente.
    - 200 OK con el producto en el body (activo=false): el producto tenía
      movimientos asociados, así que se dio de baja lógica en lugar de
      eliminarlo, para preservar la trazabilidad del historial.
    - 404 Not Found: no existía un producto con ese id.
    - 503 Service Unavailable: no se pudo consultar a Movimientos para saber
      si hay historial. No se elimina ni se da de baja nada (falla cerrada):
      borrar físicamente un producto con movimientos perdería trazabilidad.

    "Tiene movimientos asociados" se resuelve consultando de verdad al
    servicio de Movimientos (con el token del admin que hace la baja).
    """
    try:
        eliminado = producto_service.eliminar_producto(db, id_producto, token)
    except MovimientosClientError as error:
        detalle = error.mensaje
        if error.status_code >= 500:
            detalle += (
                " No se eliminó el producto para no perder la trazabilidad del historial; "
                "reintentá en unos instantes."
            )
        raise HTTPException(status_code=error.status_code, detail=detalle)
    if not eliminado:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró un producto con id_producto={id_producto}.",
        )

    if eliminado.activo is False:
        # Baja lógica: se devuelve 200 con el recurso actualizado en vez de
        # 204, para que el cliente pueda distinguir ambos casos y mostrar
        # un mensaje distinto ("dado de baja" vs "eliminado").
        response.status_code = status.HTTP_200_OK
        return ProductoOut.model_validate(eliminado)

    return None


@router.patch(
    "/{id_producto}/ajustar-stock",
    response_model=ProductoOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(requerir_rol("admin", "empleado"))],
)
def ajustar_stock(
    id_producto: int,
    ajuste: AjusteStockIn,
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key", max_length=100),
):
    """
    PATCH /api/v1/productos/{id}/ajustar-stock -> 200 OK, 404 Not Found,
    409 Conflict (sin stock) o 422 (Idempotency-Key reutilizada con otro ajuste).

    Endpoint consumido por el servicio de Movimientos para reflejar ventas,
    ingresos, estropeos y devoluciones. El ajuste se aplica de forma atómica
    (ver producto_service.ajustar_stock_atomico), por lo que es seguro ante
    llamadas concurrentes: nunca deja stock_actual negativo.

    Requiere rol admin o empleado, igual que el resto de los endpoints de
    escritura. El servicio de Movimientos reenvía el JWT del usuario que
    originó el movimiento.

    Idempotencia: si se envía el header opcional Idempotency-Key, el ajuste se
    aplica UNA sola vez por clave (la clave se guarda en la misma transacción
    que el cambio de stock). Repetir el pedido con la misma clave y el mismo
    contenido no vuelve a modificar el stock y devuelve 200 con el producto
    actual. Es lo que permite a Movimientos reintentar o recuperar un
    movimiento pendiente sin riesgo de descontar dos veces.
    """
    resultado = producto_service.ajustar_stock_atomico(
        db, id_producto, ajuste.delta, clave=idempotency_key
    )

    if resultado is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró un producto con id_producto={id_producto}.",
        )
    if resultado == producto_service.CLAVE_REUTILIZADA:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La Idempotency-Key ya fue usada para un ajuste distinto (otro producto u otra cantidad).",
        )
    if resultado == "sin_stock":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stock insuficiente para realizar el ajuste solicitado.",
        )
    return producto_service.obtener_producto_por_id(db, id_producto)