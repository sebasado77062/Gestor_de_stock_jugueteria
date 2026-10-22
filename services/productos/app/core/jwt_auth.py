"""
Validación de JWT en services/productos (issue #7).

Diseño: productos NO llama al servicio auth por cada request (eso
acoplaría los servicios y agregaría una llamada de red y un punto de falla
extra a cada endpoint protegido). En su lugar, valida el JWT localmente,
usando la misma AUTH_SECRET_KEY que auth usó para firmarlo — ambos
servicios comparten ese secreto vía variable de entorno (ver
docker-compose.yml y .env.example), no una base de datos ni una API.

La única dependencia de red real hacia otro componente es Redis, para
consultar la blacklist de tokens revocados: eso sí necesita ser
centralizado, porque un logout debe invalidar el token en todos los
servicios que lo acepten, no solo en auth. Productos y auth apuntan al
mismo Redis (misma REDIS_URL).

Este archivo es, deliberadamente, una versión reducida y de solo lectura
del módulo equivalente en services/auth/app/core/dependencies.py: no
puede emitir ni revocar tokens (eso es exclusivo de auth), solo validarlos.
"""
import logging
import os
from typing import Optional

import httpx
import redis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt

SECRET_KEY = os.environ["AUTH_SECRET_KEY"]
ALGORITHM = "HS256"

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Solo modo local sin Docker (ver ejecutar_local.ps1): URL del servicio auth.
# Cuando REDIS_URL=memory://, cada servicio tiene un Redis en memoria propio y
# no ve los tokens que auth revoca en el suyo. Para que el logout surta efecto
# igual, en ese modo se le pregunta a auth si el token fue revocado (ver
# GET /api/v1/auth/tokens/{jti}/revocado). Con Redis real compartido (Docker)
# esta variable no se usa.
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "").rstrip("/")
AUTH_TIMEOUT_SEGUNDOS = float(os.getenv("AUTH_SERVICE_TIMEOUT", "2"))
_REVOCACION_VIA_AUTH = REDIS_URL.startswith("memory://") and bool(AUTH_SERVICE_URL)

if REDIS_URL.startswith("memory://"):
    # Modo local sin Redis (solo desarrollo): ver services/auth/app/core/redis_client.py.
    # Es un Redis propio de este proceso. Si AUTH_SERVICE_URL está definida,
    # la revocación se consulta a auth en lugar de a este Redis.
    import fakeredis
    _redis_client = fakeredis.FakeRedis(decode_responses=True)
else:
    _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

bearer_scheme = HTTPBearer(auto_error=False)


def _token_revocado_segun_auth(jti: str) -> bool:
    """
    Modo local: pregunta a auth si el jti fue revocado. Mismo criterio de
    fail-open que con Redis: si auth no responde, no se bloquea el servicio
    (la firma y expiración del JWT se siguen validando siempre).
    """
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
        # Si Redis está caído, se prioriza no romper todo el servicio de
        # productos por una dependencia de infraestructura secundaria: se
        # deja pasar el token (fail-open) en lugar de devolver 500 a cada
        # request. La validación de firma/expiración del JWT en sí sigue
        # aplicando siempre — lo único que se pierde temporalmente es la
        # capacidad de revocar tokens antes de que expiren solos.
        return False


def obtener_token_crudo(
    credenciales: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[str]:
    """
    Devuelve el JWT tal como llegó en el header Authorization (o None si no
    vino). No valida nada: la validación la hace obtener_usuario_actual. Se usa
    cuando hay que reenviar el token del usuario a otro servicio (ver
    app/clients/movimientos_client.py).
    """
    return credenciales.credentials if credenciales else None


def obtener_usuario_actual(credenciales: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> dict:
    """
    Dependencia de FastAPI: valida el JWT del header Authorization: Bearer.
    Devuelve el payload decodificado (sub, email, rol, jti) si es válido.
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

    return payload


def requerir_rol(*roles_permitidos: str):
    """
    requerir_rol("admin") exige ese rol; requerir_rol("admin", "empleado")
    acepta cualquiera de los dos. Mismo patrón que en services/auth.
    """
    def verificar(payload: dict = Depends(obtener_usuario_actual)) -> dict:
        if payload.get("rol") not in roles_permitidos:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Esta acción requiere uno de estos roles: {', '.join(roles_permitidos)}.",
            )
        return payload

    return verificar
