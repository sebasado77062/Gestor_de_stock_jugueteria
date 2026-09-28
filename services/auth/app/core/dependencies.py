"""
Dependencias de FastAPI para proteger endpoints con JWT.

Este módulo es la base del que se copia una versión reducida (sin acceso a
la base de datos de usuarios, que es privada del servicio auth) hacia
services/productos y services/movimientos, para que puedan validar el
access token localmente sin llamar a auth por cada request (ver
services/productos/app/core/jwt_auth.py una vez implementado el punto de
integración). Aquí, en auth, además consulta la blacklist de Redis.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.security import decodificar_token
from app.core.redis_client import token_esta_revocado

bearer_scheme = HTTPBearer(auto_error=False)


def obtener_usuario_actual(credenciales: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> dict:
    """
    Valida el access token del header Authorization: Bearer <token> y
    devuelve su payload (sub, email, rol, jti, etc.) si es válido.

    Rechaza con 401 si: no hay token, el token no decodifica (firma
    inválida o expiró), no es de tipo "access", o su jti está en la
    blacklist de Redis (fue revocado por un logout).
    """
    if credenciales is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se proporcionó un token de autenticación.",
        )

    try:
        payload = decodificar_token(credenciales.credentials)
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

    if token_esta_revocado(payload["jti"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token fue revocado (sesión cerrada). Iniciá sesión de nuevo.",
        )

    return payload


def requerir_rol(*roles_permitidos: str):
    """
    Factory de dependencia: requerir_rol("admin") exige que el usuario
    autenticado tenga ese rol; requerir_rol("admin", "empleado") acepta
    cualquiera de los dos.

    Uso en un router:
        @router.get("/algo", dependencies=[Depends(requerir_rol("admin"))])
    """
    def verificar(payload: dict = Depends(obtener_usuario_actual)) -> dict:
        if payload.get("rol") not in roles_permitidos:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Esta acción requiere uno de estos roles: {', '.join(roles_permitidos)}.",
            )
        return payload

    return verificar
