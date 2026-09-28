"""
Cliente de Redis y utilidades de seguridad basadas en él, para el servicio
auth (issue #7):

1. Revocación de tokens (blacklist) con TTL: al hacer logout, el access
   token se agrega a una lista negra hasta que hubiera expirado de todos
   modos (después de eso, Redis lo borra solo).
2. Bloqueo por intentos fallidos de login: cuenta intentos fallidos por
   usuario con un contador que expira solo, y bloquea el login por un
   tiempo si se supera el máximo.
3. Rotación de refresh token de un solo uso: cada refresh token válido se
   guarda en Redis; al usarlo para pedir un nuevo access token, se borra
   inmediatamente y se emite un refresh token nuevo. Si alguien intenta
   reusar un refresh token ya consumido, la operación falla.

Todo el estado de estos tres mecanismos vive en Redis (no en la base de
datos SQL), porque son datos de vida corta con expiración automática, algo
para lo que Redis está pensado y una base relacional no.
"""
import os
import uuid

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# decode_responses=True: todas las respuestas de redis-py llegan como str,
# no bytes, lo cual simplifica el resto del código (comparaciones directas
# de strings sin decodificar a mano en cada lugar que usa el cliente).
if REDIS_URL.startswith("memory://"):
    # MODO LOCAL SIN REDIS (solo desarrollo): usa una imitación de Redis en
    # memoria del propio proceso (pip install fakeredis). Sirve para probar
    # login y roles sin instalar nada más, pero el estado no se comparte con
    # otros procesos ni sobrevive a un reinicio. En Docker se usa el Redis real.
    import fakeredis
    redis_client = fakeredis.FakeRedis(decode_responses=True)
else:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# --- Configuración de bloqueo por intentos fallidos ---
MAX_INTENTOS_FALLIDOS = int(os.getenv("AUTH_MAX_INTENTOS_FALLIDOS", "5"))
VENTANA_BLOQUEO_SEGUNDOS = int(os.getenv("AUTH_VENTANA_BLOQUEO_SEGUNDOS", "300"))  # 5 min


def _clave_blacklist(jti: str) -> str:
    return f"auth:blacklist:{jti}"


def _clave_intentos(email: str) -> str:
    return f"auth:intentos_fallidos:{email}"


def _clave_bloqueo(email: str) -> str:
    return f"auth:bloqueado:{email}"


def _clave_refresh(jti: str) -> str:
    return f"auth:refresh:{jti}"


# ---------------------------------------------------------------------------
# 1. Revocación de tokens (blacklist) con TTL
# ---------------------------------------------------------------------------

def revocar_token(jti: str, segundos_restantes: int) -> None:
    """
    Agrega el jti (identificador único) de un access token a la blacklist,
    con un TTL igual al tiempo que le quedaba de vida al token. Pasado ese
    tiempo, el token habría expirado de todos modos, así que Redis puede
    olvidarlo sin que haga falta borrarlo a mano.
    """
    if segundos_restantes <= 0:
        return  # el token ya expiró de todos modos, no hace falta guardar nada
    redis_client.setex(_clave_blacklist(jti), segundos_restantes, "1")


def token_esta_revocado(jti: str) -> bool:
    """True si el jti fue revocado (logout) y todavía no expiró la marca."""
    return redis_client.exists(_clave_blacklist(jti)) == 1


# ---------------------------------------------------------------------------
# 2. Bloqueo por intentos fallidos de login
# ---------------------------------------------------------------------------

def registrar_intento_fallido(email: str) -> int:
    """
    Incrementa el contador de intentos fallidos de un email. El contador
    expira solo a los VENTANA_BLOQUEO_SEGUNDOS de la primera falla (si no
    hay más fallas, no queda bloqueado indefinidamente). Si al incrementar
    se alcanza el máximo permitido, activa el bloqueo.

    Devuelve la cantidad de intentos fallidos acumulados hasta ahora.
    """
    clave = _clave_intentos(email)
    intentos = redis_client.incr(clave)
    if intentos == 1:
        # Primer intento fallido de esta ventana: le ponemos vencimiento.
        redis_client.expire(clave, VENTANA_BLOQUEO_SEGUNDOS)

    if intentos >= MAX_INTENTOS_FALLIDOS:
        redis_client.setex(_clave_bloqueo(email), VENTANA_BLOQUEO_SEGUNDOS, "1")

    return intentos


def limpiar_intentos_fallidos(email: str) -> None:
    """Se llama tras un login exitoso: reinicia el contador de fallos."""
    redis_client.delete(_clave_intentos(email))
    redis_client.delete(_clave_bloqueo(email))


def esta_bloqueado(email: str) -> bool:
    """True si el email está bloqueado por exceso de intentos fallidos."""
    return redis_client.exists(_clave_bloqueo(email)) == 1


def segundos_restantes_bloqueo(email: str) -> int:
    """TTL restante del bloqueo, en segundos (0 si no está bloqueado)."""
    ttl = redis_client.ttl(_clave_bloqueo(email))
    return max(ttl, 0)


# ---------------------------------------------------------------------------
# 3. Rotación de refresh token de un solo uso
# ---------------------------------------------------------------------------

def generar_jti() -> str:
    """Identificador único para un token (usado como jti en el JWT)."""
    return str(uuid.uuid4())


def guardar_refresh_token(jti: str, id_usuario: int, segundos_ttl: int) -> None:
    """
    Registra un refresh token como válido y no usado todavía. El valor
    guardado es el id de usuario, para poder validarlo al consumirlo sin
    tener que decodificar el JWT de nuevo.
    """
    redis_client.setex(_clave_refresh(jti), segundos_ttl, str(id_usuario))


def consumir_refresh_token(jti: str) -> str | None:
    """
    Intenta consumir (usar y eliminar atómicamente) un refresh token.

    Devuelve el id de usuario asociado si el token era válido y no había
    sido usado antes, o None si no existía (ya fue consumido, expiró, o
    nunca existió). Usa GETDEL (atómico en Redis: leer y borrar en una sola
    operación) para que, si dos requests llegan casi al mismo tiempo con el
    mismo refresh token, solo uno de los dos pueda consumirlo exitosamente
    — el otro recibe None. Esto es lo que hace que la rotación sea segura
    ante un intento de reuso, incluso en una carrera entre dos requests
    concurrentes.
    """
    return redis_client.getdel(_clave_refresh(jti))


def revocar_refresh_token(jti: str) -> None:
    """
    Invalida un refresh token sin necesidad de consumirlo (por ejemplo, al
    hacer logout: se quiere que ese refresh token ya no sirva más).
    """
    redis_client.delete(_clave_refresh(jti))
