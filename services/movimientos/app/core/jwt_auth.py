"""
Validación de JWT en services/movimientos.

Mismo patrón que services/productos/app/core/jwt_auth.py: no se llama al
servicio auth por cada request (eso agregaría latencia y un punto de falla
extra). En su lugar, se valida el JWT localmente usando la misma
AUTH_SECRET_KEY que auth usó para firmarlo (variable de entorno compartida).
La única dependencia de red es Redis, para consultar la blacklist de tokens
revocados (mismo Redis que auth y productos). En el modo local sin Docker, la
revocación se consulta a auth (ver AUTH_SERVICE_URL más abajo).

A diferencia de productos, movimientos NO necesita exigir un rol específico
para crear un movimiento: alcanza con que el usuario esté autenticado. Se
expone igual el helper requerir_rol por consistencia, por si en el futuro
alguna operación se restringe (ej. anular un movimiento solo admin).
"""
import logging
import os

import httpx
import redis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt

SECRET_KEY = os.environ["AUTH_SECRET_KEY"]
ALGORITHM = "HS256"

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Solo modo local sin Docker (ver ejecutar_local.ps1): con REDIS_URL=memory://
# cada servicio tiene un Redis propio y no ve los tokens que auth revoca. En ese
# modo, si AUTH_SERVICE_URL está definida, se le pregunta a auth si el token fue
# revocado (GET /api/v1/auth/tokens/{jti}/revocado). Con Redis compartido
# (Docker) esta variable no se usa. Mismo mecanismo que services/productos.
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "").rstrip("/")
AUTH_TIMEOUT_SEGUNDOS = float(os.getenv("AUTH_SERVICE_TIMEOUT", "2"))
_REVOCACION_VIA_AUTH = REDIS_URL.startswith("memory://") and bool(AUTH_SERVICE_URL)

if REDIS_URL.startswith("memory://"):
    import fakeredis
    _redis_client = fakeredis.FakeRedis(decode_responses=True)
else:
    _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

bearer_scheme = HTTPBearer(auto_error=False)


def _token_revocado_segun_auth(jti: str) -> bool:
    """Modo local: pregunta a auth si el jti fue revocado. Fail-open si auth no responde."""
    try:
        respuesta = httpx.get(
            f"{AUTH_SERVICE_URL}/api/v1/auth/tokens/{jti}/revocado",
            timeout=AUTH_TIMEOUT_SEGUNDOS,
        )
        if respuesta.status_code == 200:
            return respuesta.json().get("revocado") is True
        logger.warning(
            "auth respondió %s al consultar la revocación; ¿está HABILITAR_CONSULTA_REVOCACION=true en auth?",
            respuesta.status_code,
        )
    except (httpx.HTTPError, ValueError):
        logger.warning("No se pudo consultar a auth si el token fue revocado; se deja pasar (fail-open).")
    return False


def _token_esta_revocado(jti: str) -> bool:
    if _REVOCACION_VIA_AUTH:
        return _token_revocado_segun_auth(jti)
    try:
        return _redis_client.exists(f"auth:blacklist:{jti}") == 1
    except redis.RedisError:
        # Fail-open ante Redis caído: se prioriza no bloquear todo el
        # servicio por una dependencia secundaria. La firma y expiración
        # del JWT se siguen validando siempre.
        return False


def obtener_token_y_payload(
    credenciales: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> tuple[str, dict]:
    """
    Dependencia de FastAPI: valida el JWT y devuelve (token_crudo, payload).

    El token crudo es necesario para reenviarlo al servicio de Productos
    al ajustar el stock: Productos también exige JWT, y queremos propagar
    el token del usuario original (no usar credenciales de servicio).
    """
    if credenciales is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se proporcionó un token de autenticación.",
        )

    try:
        payload = jwt.decode(credenciales.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado.",
        )

    if payload.get("tipo") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token proporcionado no es un access token.",
        )

    if _token_esta_revocado(payload["jti"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token fue revocado (sesión cerrada). Iniciá sesión de nuevo.",
        )

    return credenciales.credentials, payload


def requerir_rol(*roles_permitidos: str):
    """
    requerir_rol("admin") exige ese rol; requerir_rol("admin", "empleado")
    acepta cualquiera de los dos. Mismo patrón que en services/auth y
    services/productos.
    """
    def verificar(datos: tuple[str, dict] = Depends(obtener_token_y_payload)) -> tuple[str, dict]:
        _, payload = datos
        if payload.get("rol") not in roles_permitidos:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Esta acción requiere uno de estos roles: {', '.join(roles_permitidos)}.",
            )
        return datos

    return verificar