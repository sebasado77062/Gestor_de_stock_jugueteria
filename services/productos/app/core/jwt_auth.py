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
import os

import redis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt

SECRET_KEY = os.environ["AUTH_SECRET_KEY"]
ALGORITHM = "HS256"

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
if REDIS_URL.startswith("memory://"):
    # Modo local sin Redis (solo desarrollo): ver services/auth/app/core/redis_client.py.
    # Al ser un Redis propio de este proceso, no ve los tokens revocados por
    # auth: el logout no invalida el token acá hasta que expira solo.
    import fakeredis
    _redis_client = fakeredis.FakeRedis(decode_responses=True)
else:
    _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

bearer_scheme = HTTPBearer(auto_error=False)


def _token_esta_revocado(jti: str) -> bool:
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
