"""
Utilidades de seguridad: hashing de contraseñas y emisión/validación de JWT.

Dos tipos de token, distinguidos por el claim "tipo" dentro del JWT:
- access: de vida corta (default 15 min), se manda en cada request protegida.
- refresh: de vida más larga (default 7 días), se usa solo para pedir un
  access token nuevo sin volver a loguearse. Cada refresh token es de un
  solo uso (ver app/core/redis_client.py, sección de rotación).
"""
import os
import logging
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

# passlib 1.7.4 intenta leer bcrypt.__about__.__version__ para detectar la
# versión del backend; los paquetes bcrypt modernos (>=4.1) ya no exponen
# ese atributo, así que passlib captura el AttributeError y lo loguea como
# error, aunque el hashing funciona bien igual. Se silencia ese logger
# puntual para no ensuciar la salida con un error cosmético sin impacto
# funcional (el hashing y la verificación de contraseñas están probados
# end-to-end en tests/test_auth.py).
logging.getLogger("passlib").setLevel(logging.ERROR)

# --- Hashing de contraseñas ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hashear_password(password: str) -> str:
    return pwd_context.hash(password)


def verificar_password(password_plano: str, password_hasheado: str) -> bool:
    return pwd_context.verify(password_plano, password_hasheado)


# --- JWT ---
# SECRET_KEY se toma de una variable de entorno obligatoria (ver
# .env.example): a diferencia del resto del proyecto, este SÍ es un
# secreto real y nunca debe tener un valor por defecto hardcodeado en el
# código, ni siquiera para desarrollo.
SECRET_KEY = os.environ["AUTH_SECRET_KEY"]
ALGORITHM = "HS256"

ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))


def crear_access_token(id_usuario: int, email: str, rol: str, jti: str) -> str:
    ahora = datetime.now(timezone.utc)
    payload = {
        "sub": str(id_usuario),
        "email": email,
        "rol": rol,
        "tipo": "access",
        "jti": jti,
        "iat": ahora,
        "exp": ahora + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def crear_refresh_token(id_usuario: int, jti: str) -> str:
    ahora = datetime.now(timezone.utc)
    payload = {
        "sub": str(id_usuario),
        "tipo": "refresh",
        "jti": jti,
        "iat": ahora,
        "exp": ahora + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def segundos_hasta_expirar(payload: dict) -> int:
    """Segundos que le quedan de vida a un token ya decodificado."""
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    return max(int((exp - datetime.now(timezone.utc)).total_seconds()), 0)


def decodificar_token(token: str) -> dict:
    """
    Decodifica y valida la firma/expiración de un JWT.

    Lanza jose.JWTError (firma inválida, token malformado o expirado) si el
    token no es válido; el llamador es responsable de capturarla y
    convertirla en una respuesta HTTP 401 apropiada.
    """
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
