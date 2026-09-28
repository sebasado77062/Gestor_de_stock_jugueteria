"""
Capa de servicio del módulo de autenticación: login, refresh, logout, y
gestión de usuarios. Concentra toda la interacción entre la base de datos
SQL (usuarios), Redis (bloqueo, blacklist, rotación de refresh) y la
emisión/validación de JWT.
"""
from sqlalchemy.orm import Session

from app.core import redis_client
from app.core.security import (
    hashear_password,
    verificar_password,
    crear_access_token,
    crear_refresh_token,
    decodificar_token,
    segundos_hasta_expirar,
)
from app.models.usuario import Usuario
from app.schemas.usuario import UsuarioCreate


class CredencialesInvalidas(Exception):
    """Email inexistente, contraseña incorrecta, o usuario inactivo."""


class CuentaBloqueada(Exception):
    """Demasiados intentos fallidos recientes; login temporalmente bloqueado."""

    def __init__(self, segundos_restantes: int):
        self.segundos_restantes = segundos_restantes


class TokenInvalido(Exception):
    """Refresh token expirado, con firma inválida, ya usado, o revocado."""


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------

def obtener_usuario_por_email(db: Session, email: str) -> Usuario | None:
    return db.query(Usuario).filter(Usuario.email == email).first()


def obtener_usuario_por_id(db: Session, id_usuario: int) -> Usuario | None:
    return db.query(Usuario).filter(Usuario.id_usuario == id_usuario).first()


def crear_usuario(db: Session, datos: UsuarioCreate) -> Usuario:
    nuevo = Usuario(
        email=datos.email,
        password_hash=hashear_password(datos.password),
        nombre=datos.nombre,
        rol=datos.rol,
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo


def listar_usuarios(db: Session) -> list[Usuario]:
    return db.query(Usuario).all()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def login(db: Session, email: str, password: str) -> tuple[str, str]:
    """
    Valida credenciales y, si son correctas, emite un par (access, refresh)
    de tokens nuevos.

    Levanta CuentaBloqueada si el email superó el máximo de intentos
    fallidos recientes (issue #7: "bloqueo por intentos fallidos"), y
    CredencialesInvalidas si el email no existe, la contraseña no coincide,
    o el usuario está inactivo.
    """
    if redis_client.esta_bloqueado(email):
        raise CuentaBloqueada(redis_client.segundos_restantes_bloqueo(email))

    usuario = obtener_usuario_por_email(db, email)

    # OJO: se ejecuta verificar_password incluso si el usuario no existe
    # (contra un hash "dummy"), para que el tiempo de respuesta no revele
    # si el email está o no registrado (mitigación básica de enumeración
    # de usuarios por timing).
    hash_a_verificar = usuario.password_hash if usuario else pwd_context_dummy_hash()
    password_valido = verificar_password(password, hash_a_verificar)

    if not usuario or not password_valido or not usuario.activo:
        intentos = redis_client.registrar_intento_fallido(email)
        if intentos >= redis_client.MAX_INTENTOS_FALLIDOS:
            raise CuentaBloqueada(redis_client.segundos_restantes_bloqueo(email))
        raise CredencialesInvalidas()

    redis_client.limpiar_intentos_fallidos(email)
    return _emitir_par_de_tokens(usuario)


def _emitir_par_de_tokens(usuario: Usuario) -> tuple[str, str]:
    """
    Genera un access token y un refresh token nuevos para el usuario.
    El refresh token se registra en Redis como válido y no usado (ver
    app/core/redis_client.py, rotación de un solo uso).
    """
    from app.core.security import REFRESH_TOKEN_EXPIRE_DAYS
    from app.core.redis_client import generar_jti, guardar_refresh_token

    access_jti = generar_jti()
    refresh_jti = generar_jti()

    access_token = crear_access_token(usuario.id_usuario, usuario.email, usuario.rol.value, access_jti)
    refresh_token = crear_refresh_token(usuario.id_usuario, refresh_jti)

    guardar_refresh_token(refresh_jti, usuario.id_usuario, REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600)

    return access_token, refresh_token


_dummy_hash_cache: str | None = None


def pwd_context_dummy_hash() -> str:
    """
    Hash fijo (calculado una sola vez, cacheado en memoria del proceso)
    usado para comparar contra una contraseña cuando el usuario no existe,
    de forma que verificar_password() tarde un tiempo similar exista o no
    la cuenta.
    """
    global _dummy_hash_cache
    if _dummy_hash_cache is None:
        _dummy_hash_cache = hashear_password("valor-que-nunca-coincide-con-nada")
    return _dummy_hash_cache


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def refrescar_access_token(db: Session, refresh_token: str) -> tuple[str, str]:
    """
    Consume un refresh token (de un solo uso) y, si era válido, emite un
    par NUEVO de tokens (access + refresh), rotando el refresh token.

    Levanta TokenInvalido si el JWT no decodifica, no es de tipo "refresh",
    o ya fue consumido/expiró en Redis (reuso de un refresh token ya usado,
    o token desconocido).
    """
    try:
        payload = decodificar_token(refresh_token)
    except Exception:
        raise TokenInvalido("Refresh token inválido o expirado.")

    if payload.get("tipo") != "refresh":
        raise TokenInvalido("El token proporcionado no es un refresh token.")

    jti = payload["jti"]
    id_usuario_en_redis = redis_client.consumir_refresh_token(jti)

    if id_usuario_en_redis is None:
        # No existía en Redis: o ya fue usado antes (reuso detectado), o
        # expiró, o nunca fue emitido por este servicio. En cualquier caso,
        # no se emite un token nuevo.
        raise TokenInvalido("El refresh token ya fue utilizado, expiró, o no es válido.")

    usuario = obtener_usuario_por_id(db, int(id_usuario_en_redis))
    if not usuario or not usuario.activo:
        raise TokenInvalido("El usuario asociado a este token ya no existe o está inactivo.")

    return _emitir_par_de_tokens(usuario)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

def logout(access_token: str, refresh_token: str | None = None) -> None:
    """
    Revoca el access token actual (queda en blacklist hasta que hubiera
    expirado de todos modos) y, si se provee, invalida también el refresh
    token asociado para que no se pueda usar para obtener nuevos access
    tokens tras el logout.
    """
    try:
        payload = decodificar_token(access_token)
    except Exception:
        return  # token ya inválido/expirado: nada que revocar

    if payload.get("tipo") == "access":
        redis_client.revocar_token(payload["jti"], segundos_hasta_expirar(payload))

    if refresh_token:
        try:
            refresh_payload = decodificar_token(refresh_token)
            if refresh_payload.get("tipo") == "refresh":
                redis_client.revocar_refresh_token(refresh_payload["jti"])
        except Exception:
            pass  # refresh token ya inválido: no hay nada que revocar
