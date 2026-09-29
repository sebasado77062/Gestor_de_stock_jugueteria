"""
Idempotencia de POST /api/v1/movimientos vía el header Idempotency-Key.

Si el cliente reintenta la misma solicitud (por un timeout, un corte de red,
un doble clic en la UI, etc.) enviando la misma Idempotency-Key, no queremos
volver a ajustar el stock ni crear un segundo movimiento: devolvemos el
mismo movimiento que ya se había registrado la primera vez.

Se guarda en Redis un mapeo Idempotency-Key -> id_movimiento, con TTL (por
defecto 24 h) para no acumular claves indefinidamente.
"""
import os
from typing import Optional

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
TTL_SEGUNDOS = int(os.getenv("IDEMPOTENCY_TTL_SEGUNDOS", str(24 * 60 * 60)))  # 24 horas
PREFIJO_CLAVE = "idempotencia:movimientos:"

_cliente_redis: Optional["redis.Redis"] = None


def _obtener_cliente() -> "redis.Redis":
    global _cliente_redis
    if _cliente_redis is None:
        _cliente_redis = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
    return _cliente_redis


def obtener_id_movimiento_previo(idempotency_key: Optional[str]) -> Optional[int]:
    """Si esta Idempotency-Key ya se procesó, devuelve el id_movimiento generado en ese momento."""
    if not idempotency_key:
        return None
    valor = _obtener_cliente().get(f"{PREFIJO_CLAVE}{idempotency_key}")
    return int(valor) if valor is not None else None


def registrar_idempotency_key(idempotency_key: Optional[str], id_movimiento: int) -> None:
    if not idempotency_key:
        return
    _obtener_cliente().set(f"{PREFIJO_CLAVE}{idempotency_key}", str(id_movimiento), ex=TTL_SEGUNDOS)
