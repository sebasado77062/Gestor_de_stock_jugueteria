"""
Cliente HTTP hacia el servicio de Productos.

Movimientos nunca accede directamente a la base de datos de Productos: toda
interacción pasa por esta capa, que llama a la API REST de Productos con un
timeout explícito y traduce cualquier falla (timeout, conexión rechazada,
401, 403, 404, 409, 5xx) a un error consistente (ProductosClientError) que
el router convierte 1 a 1 en una respuesta HTTP para el cliente.

Autenticación: Productos exige JWT en todos sus endpoints. El token del
usuario que originó el movimiento se propaga desde el router de Movimientos
hacia acá, y de acá hacia Productos en el header Authorization. No se usan
credenciales de servicio: se reenvía el token del usuario original, así
Productos aplica sus propias reglas de rol sobre ese usuario.

PRODUCTOS_SERVICE_URL apunta, dentro de la red interna de Docker Compose,
al nombre del servicio ("http://productos:8000"); en desarrollo local sin
Docker, por defecto usa "http://localhost:8000".
"""
import os

import httpx

PRODUCTOS_SERVICE_URL = os.getenv("PRODUCTOS_SERVICE_URL", "http://localhost:8000")
TIMEOUT_SEGUNDOS = float(os.getenv("PRODUCTOS_SERVICE_TIMEOUT", "5"))


class ProductosClientError(Exception):
    """
    Error estandarizado de la comunicación con Productos. status_code ya
    viene resuelto al código HTTP que corresponde devolver desde Movimientos
    (ej. 401 si el token venció, 403 si el rol no alcanza, 404 si el producto
    no existe, 409 si no hay stock, 503 si Productos no respondió a tiempo).
    """

    def __init__(self, status_code: int, mensaje: str):
        self.status_code = status_code
        self.mensaje = mensaje
        super().__init__(mensaje)


def _headers_con_token(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _traducir_error(respuesta: httpx.Response, id_producto: int, contexto: str) -> ProductosClientError:
    """
    Traduce una respuesta HTTP de error de Productos a un ProductosClientError.
    contexto: "consultar el producto" o "ajustar el stock", para que el
    mensaje final quede claro.
    """
    if respuesta.status_code == 401:
        return ProductosClientError(
            401, "Tu sesión venció o el token es inválido. Iniciá sesión de nuevo."
        )
    if respuesta.status_code == 403:
        return ProductosClientError(
            403, "No tenés permisos para esta operación sobre el stock."
        )
    if respuesta.status_code == 404:
        return ProductosClientError(404, f"No existe el producto con id_producto={id_producto}.")
    if respuesta.status_code == 409:
        return ProductosClientError(409, "Stock insuficiente para registrar este movimiento.")
    return ProductosClientError(
        502, f"El servicio de Productos devolvió un error inesperado al {contexto}."
    )


def obtener_producto(id_producto: int, token: str) -> dict:
    """GET /api/v1/productos/{id} en el servicio de Productos."""
    url = f"{PRODUCTOS_SERVICE_URL}/api/v1/productos/{id_producto}"
    try:
        respuesta = httpx.get(
            url,
            headers=_headers_con_token(token),
            timeout=TIMEOUT_SEGUNDOS,
        )
    except httpx.TimeoutException as error:
        raise ProductosClientError(503, "El servicio de Productos no respondió a tiempo.") from error
    except httpx.RequestError as error:
        raise ProductosClientError(503, "No se pudo conectar con el servicio de Productos.") from error

    if respuesta.status_code >= 400:
        raise _traducir_error(respuesta, id_producto, "consultar el producto")
    return respuesta.json()


def ajustar_stock(id_producto: int, delta: int, token: str) -> dict:
    """
    PATCH /api/v1/productos/{id}/ajustar-stock en el servicio de Productos.

    delta > 0 incrementa el stock (ingreso, devolución).
    delta < 0 intenta decrementarlo (venta, estropeo); si Productos responde
    409 (ajuste atómico rechazado por falta de stock) se propaga tal cual.
    """
    url = f"{PRODUCTOS_SERVICE_URL}/api/v1/productos/{id_producto}/ajustar-stock"
    try:
        respuesta = httpx.patch(
            url,
            json={"delta": delta},
            headers=_headers_con_token(token),
            timeout=TIMEOUT_SEGUNDOS,
        )
    except httpx.TimeoutException as error:
        raise ProductosClientError(
            503, "El servicio de Productos no respondió a tiempo al ajustar el stock."
        ) from error
    except httpx.RequestError as error:
        raise ProductosClientError(
            503, "No se pudo conectar con el servicio de Productos para ajustar el stock."
        ) from error

    if respuesta.status_code >= 400:
        raise _traducir_error(respuesta, id_producto, "ajustar el stock")
    return respuesta.json()