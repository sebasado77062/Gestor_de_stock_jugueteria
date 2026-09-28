"""
Rutas del servicio auth.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.core.dependencies import requerir_rol
from app.schemas.usuario import (
    UsuarioCreate,
    UsuarioOut,
    LoginRequest,
    TokenResponse,
    RefreshRequest,
    AccessTokenResponse,
)
from app.services import auth_service
from app.services.auth_service import CredencialesInvalidas, CuentaBloqueada, TokenInvalido

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
bearer_scheme = HTTPBearer(auto_error=False)


@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
def login(datos: LoginRequest, db: Session = Depends(get_db)):
    """
    Autentica con email/password y devuelve un par de tokens (access +
    refresh). Bloquea temporalmente tras demasiados intentos fallidos
    seguidos (ver app/core/redis_client.py).
    """
    try:
        access_token, refresh_token = auth_service.login(db, datos.email, datos.password)
    except CuentaBloqueada as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Demasiados intentos fallidos. Probá de nuevo en {e.segundos_restantes} segundos.",
        )
    except CredencialesInvalidas:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos.",
        )

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=AccessTokenResponse, status_code=status.HTTP_200_OK)
def refresh(datos: RefreshRequest, db: Session = Depends(get_db)):
    """
    Intercambia un refresh token válido (y no usado antes) por un access
    token nuevo. El refresh token usado queda invalidado inmediatamente
    (rotación de un solo uso, issue #7): la respuesta no reutiliza el mismo
    refresh token, siempre emite uno nuevo también.

    Nota de diseño: se responde solo con el nuevo access_token en el body
    para mantener el schema simple; el nuevo refresh_token se agrega en un
    header propio (X-New-Refresh-Token) para no romper el contrato de
    AccessTokenResponse. El frontend debe leerlo de ahí y reemplazar el
    refresh token guardado.
    """
    from fastapi import Response

    try:
        access_token, nuevo_refresh_token = auth_service.refrescar_access_token(db, datos.refresh_token)
    except TokenInvalido as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    respuesta = AccessTokenResponse(access_token=access_token)
    return Response(
        content=respuesta.model_dump_json(),
        media_type="application/json",
        headers={"X-New-Refresh-Token": nuevo_refresh_token},
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    datos: RefreshRequest | None = None,
    credenciales: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Revoca el access token actual (queda en blacklist) y, si se envía en
    el body, también el refresh token asociado, para cerrar la sesión por
    completo en ambos frentes.
    """
    if credenciales is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se proporcionó un token de autenticación.",
        )

    refresh_token = datos.refresh_token if datos else None
    auth_service.logout(credenciales.credentials, refresh_token)
    return None


@router.get("/me", response_model=UsuarioOut, status_code=status.HTTP_200_OK)
def quien_soy(
    db: Session = Depends(get_db),
    payload: dict = Depends(requerir_rol("admin", "empleado")),
):
    """Devuelve los datos del usuario autenticado actual."""
    usuario = auth_service.obtener_usuario_por_id(db, int(payload["sub"]))
    if not usuario:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")
    return usuario


# ---------------------------------------------------------------------------
# Gestión de usuarios (solo admin)
# ---------------------------------------------------------------------------

@router.post(
    "/usuarios",
    response_model=UsuarioOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requerir_rol("admin"))],
)
def crear_usuario(datos: UsuarioCreate, db: Session = Depends(get_db)):
    """Crea un usuario nuevo (admin o empleado). Solo accesible por un admin."""
    if auth_service.obtener_usuario_por_email(db, datos.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un usuario con ese email.",
        )
    return auth_service.crear_usuario(db, datos)


@router.get(
    "/usuarios",
    response_model=list[UsuarioOut],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(requerir_rol("admin"))],
)
def listar_usuarios(db: Session = Depends(get_db)):
    """Lista todos los usuarios. Solo accesible por un admin."""
    return auth_service.listar_usuarios(db)
