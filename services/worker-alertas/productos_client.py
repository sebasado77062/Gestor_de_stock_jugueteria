"""
Cliente HTTP de solo lectura hacia el servicio de Productos.

El worker de Alertas solo necesita consultar stock_actual y stock_minimo
para decidir si corresponde generar una alerta; nunca escribe en Productos.
"""
import os

import httpx

PRODUCTOS_SERVICE_URL = os.getenv("PRODUCTOS_SERVICE_URL", "http://localhost:8000")
TIMEOUT_SEGUNDOS = float(os.getenv("PRODUCTOS_SERVICE_TIMEOUT", "5"))


def obtener_producto(id_producto: int) -> dict:
    """
    GET /api/v1/productos/{id}. Lanza la excepción de httpx tal cual si
    falla (timeout, conexión rechazada, 404, 5xx): el worker decide cómo
    tratar ese error (reintento con backoff, ver worker.py).
    """
    url = f"{PRODUCTOS_SERVICE_URL}/api/v1/productos/{id_producto}"
    respuesta = httpx.get(url, timeout=TIMEOUT_SEGUNDOS)
    respuesta.raise_for_status()
    return respuesta.json()
