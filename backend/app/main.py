from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database.db import Base, engine
from app.routers import producto_router

# Crea las tablas definidas en los modelos si todavía no existen.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="API - Sistema de Gestión de Stock para Juguetería",
    description="AE1 - CRUD funcional de la entidad Producto",
    version="1.0.0",
)

# CORS: permite que el frontend, servido desde otro origen/puerto, llame a la API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción conviene restringir al dominio real del frontend.
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Middleware centralizado de manejo de errores
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Captura errores de validación de Pydantic (campos faltantes, tipos
    inválidos, stock negativo, etc.) y los traduce a 400 Bad Request.
    """
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
    """Estandariza las respuestas de error (ej. 404 Not Found) en formato JSON."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": True, "mensaje": exc.detail},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Captura cualquier excepción no controlada y devuelve 500 Internal
    Server Error sin exponer trazas internas del servidor.
    """
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": True, "mensaje": "Error interno del servidor."},
    )


# ---------------------------------------------------------------------------
# Registro de rutas
# ---------------------------------------------------------------------------
app.include_router(producto_router.router)


@app.get("/", tags=["Root"])
def root():
    """Endpoint raíz simple para verificar que el servidor está activo."""
    return {"mensaje": "API Gestor de Stock - Juguetería. Ver /docs para la documentación interactiva."}
