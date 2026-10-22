"""
Cliente HTTP hacia el servicio de Movimientos.

Lo usa `productos` para una única pregunta: ¿este producto tiene movimientos
asociados? (ver producto_service.tiene_movimientos_asociados). Productos nunca
accede a la base de datos de Movimientos: pregunta por su API REST, con el
mismo token JWT del usuario que originó la operación (no hay credenciales de
servicio, igual que en la dirección inversa).

Si Movimientos no responde, se levanta MovimientosClientError con un código
HTTP ya resuelto (503): quien llama NO debe adivinar la respuesta. Para el
borrado de productos eso significa "fallar cerrado": no se elimina nada si no
se pudo confirmar que no hay historial.

MOVIMIENTOS_SERVICE_URL apunta, dentro de Docker Compose, al nombre del
servicio ("http://movimientos:8000"); en desarrollo local sin Docker usa
"http://localhost:8002".
"""
import os
from typing import Optional

import httpx

MOVIMIENTOS_SERVICE_URL = os.getenv("MOVIMIENTOS_SERVICE_URL", "http://localhost:8002")
TIMEOUT_SEGUNDOS = float(os.getenv("MOVIMIENTOS_SERVICE_TIMEOUT", "5"))


class MovimientosClientError(Exception):
    """Error estandarizado de la comunicación con Movimientos (status_code ya resuelto)."""

    def __init__(self, status_code: int, mensaje: str):
        self.status_code = status_code
        self.mensaje = mensaje
        super().__init__(mensaje)


def contar_movimientos(id_producto: int, token: Optional[str]) -> int:
    """
    GET /api/v1/productos/{id}/movimientos/cantidad en el servicio de
    Movimientos. Devuelve la cantidad de movimientos (confirmados o
    pendientes de confirmación) del producto.
    """
    url = f"{MOVIMIENTOS_SERVICE_URL}/api/v1/productos/{id_producto}/movimientos/cantidad"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        respuesta = httpx.get(url, headers=headers, timeout=TIMEOUT_SEGUNDOS)
    except httpx.TimeoutException as error:
        raise MovimientosClientError(
            503, "El servicio de Movimientos no respondió a tiempo."
        ) from error
    except httpx.RequestError as error:
        raise MovimientosClientError(
            503, "No se pudo conectar con el servicio de Movimientos."
        ) from error

    if respuesta.status_code == 401:
        raise MovimientosClientError(401, "Tu sesión venció o el token es inválido. Iniciá sesión de nuevo.")
    if respuesta.status_code == 403:
        raise MovimientosClientError(403, "No tenés permisos para consultar los movimientos.")
    if respuesta.status_code >= 400:
        raise MovimientosClientError(
            502, "El servicio de Movimientos devolvió un error inesperado al consultar el historial."
        )

    try:
        return int(respuesta.json()["cantidad"])
    except (ValueError, KeyError, TypeError) as error:
        raise MovimientosClientError(
            502, "El servicio de Movimientos devolvió una respuesta con formato inesperado."
        ) from error
