import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database.db import Base, SessionLocal, engine
from app.database.migraciones import migrar_esquema
from app.messaging import outbox
from app.models import movimiento as _modelo_movimiento  # noqa: F401  (registra el modelo en Base)
from app.models import outbox as _modelo_outbox  # noqa: F401  (registra el modelo en Base)
from app.routers import movimiento_router

logger = logging.getLogger("movimientos")

OUTBOX_RELAY_HABILITADO = os.getenv("OUTBOX_RELAY_HABILITADO", "true").lower() == "true"
OUTBOX_INTERVALO_SEGUNDOS = float(os.getenv("OUTBOX_INTERVALO_SEGUNDOS", "5"))


def inicializar_base() -> None:
    """
    Prepara la base al arrancar: primero agrega a una base ya existente las
    columnas nuevas (migrar_esquema) y después crea las tablas que falten
    (create_all). Se hace al arrancar la app, no al importar el módulo, para
    que importar app.main (por ejemplo desde los tests) no escriba en la base real.
    """
    migrar_esquema(engine)
    Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    inicializar_base()

    relay = None
    if OUTBOX_RELAY_HABILITADO:
        relay = outbox.OutboxRelay(SessionLocal, intervalo=OUTBOX_INTERVALO_SEGUNDOS)
        outbox.registrar_relay(relay)
        relay.iniciar()
        relay.despertar()  # publica lo que haya quedado pendiente de una corrida anterior
        logger.info("Relay del outbox iniciado (intervalo %.0fs).", OUTBOX_INTERVALO_SEGUNDOS)
    try:
        yield
    finally:
        if relay is not None:
            relay.detener()
            outbox.registrar_relay(None)


app = FastAPI(
    title="API - Servicio de Movimientos de Stock",
    description="AE1/AE2 - Registro de movimientos (venta, ingreso, estropeo, devolución)",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS: permite que el frontend, servido desde otro origen/puerto, llame a la API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción conviene restringir al dominio real del frontend.
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Encabezados de seguridad (se agregan a TODA respuesta, incluidos los errores)
# ---------------------------------------------------------------------------
ENCABEZADOS_SEGURIDAD = {
    # Anti-sniffing: el navegador respeta el Content-Type declarado y no "adivina".
    "X-Content-Type-Options": "nosniff",
    # Anti-clickjacking: ningún sitio ajeno puede mostrar esta respuesta en un iframe.
    "X-Frame-Options": "DENY",
    # Equivalente moderno de X-Frame-Options (los navegadores nuevos lo priorizan).
    "Content-Security-Policy": "frame-ancestors 'none'",
}


@app.middleware("http")
async def agregar_encabezados_seguridad(request: Request, call_next):
    response = await call_next(request)
    for nombre, valor in ENCABEZADOS_SEGURIDAD.items():
        response.headers[nombre] = valor
    return response


# ---------------------------------------------------------------------------
# Middleware centralizado de manejo de errores (mismo patrón que Productos)
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Captura errores de validación de Pydantic y los traduce a 400 Bad Request."""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": True,
            "mensaje": "Datos inválidos en la solicitud.",
            "detalles": exc.errors(),
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Estandariza las respuestas de error (404, 409, 503, etc.) en formato JSON."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": True, "mensaje": exc.detail},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Captura cualquier excepción no controlada y devuelve 500 sin exponer trazas internas."""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": True, "mensaje": "Error interno del servidor."},
        # Este 500 lo arma la capa más externa de Starlette, por fuera del
        # middleware de arriba: los encabezados se agregan acá a mano.
        headers=ENCABEZADOS_SEGURIDAD,
    )


# ---------------------------------------------------------------------------
# Registro de rutas
# ---------------------------------------------------------------------------
app.include_router(movimiento_router.router)


@app.get("/", tags=["Root"])
def root():
    """Endpoint raíz simple para verificar que el servidor está activo."""
    return {"mensaje": "API Movimientos de Stock. Ver /docs para la documentación interactiva."}


@app.get("/health", tags=["Root"])
def health():
    """Healthcheck simple para el orquestador (Docker healthcheck, ver docker-compose.yml)."""
    return {"status": "ok", "service": "movimientos"}
