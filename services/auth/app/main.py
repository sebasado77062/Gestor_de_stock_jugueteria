import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database.db import Base, engine, SessionLocal
from app.models.usuario import Usuario, RolUsuario
from app.core.security import hashear_password
from app.routers import auth_router


def crear_admin_inicial_si_no_existe():
    """
    Crea un usuario admin inicial a partir de las variables de entorno
    AUTH_ADMIN_EMAIL / AUTH_ADMIN_PASSWORD, solo si todavía no existe
    ningún usuario en la base. Esto resuelve el problema de "arranque en
    frío": sin esto, nadie podría crear el primer usuario admin, porque
    POST /api/v1/auth/usuarios ya requiere estar autenticado como admin.

    Si las variables de entorno no están seteadas, no crea nada (se
    documenta como requerido en .env.example).
    """
    db = SessionLocal()
    try:
        if db.query(Usuario).count() > 0:
            return

        email = os.getenv("AUTH_ADMIN_EMAIL")
        password = os.getenv("AUTH_ADMIN_PASSWORD")
        if not email or not password:
            return

        admin = Usuario(
            email=email,
            password_hash=hashear_password(password),
            nombre="Administrador",
            rol=RolUsuario.ADMIN,
        )
        db.add(admin)
        db.commit()
    finally:
        db.close()


def crear_usuarios_de_prueba_si_no_existen():
    """
    SOLO PARA ENTORNOS DE PRUEBA. Si HABILITAR_USUARIOS_PRUEBA=true, crea un
    usuario admin y uno empleado con las credenciales definidas en las
    variables PRUEBA_ADMIN_EMAIL / PRUEBA_ADMIN_PASSWORD y
    PRUEBA_EMPLEADO_EMAIL / PRUEBA_EMPLEADO_PASSWORD (ver .env.example).

    Es idempotente: si el email ya existe, no lo toca. El frontend usa esas
    mismas variables para mostrar los botones de "acceso rápido" por rol.
    Poner HABILITAR_USUARIOS_PRUEBA=false en cualquier entorno real.
    """
    if os.getenv("HABILITAR_USUARIOS_PRUEBA", "false").lower() != "true":
        return

    usuarios_prueba = [
        ("PRUEBA_ADMIN", "Admin (prueba)", RolUsuario.ADMIN),
        ("PRUEBA_EMPLEADO", "Empleado (prueba)", RolUsuario.EMPLEADO),
    ]

    db = SessionLocal()
    try:
        for prefijo, nombre, rol in usuarios_prueba:
            email = os.getenv(f"{prefijo}_EMAIL")
            password = os.getenv(f"{prefijo}_PASSWORD")
            if not email or not password:
                continue
            if db.query(Usuario).filter(Usuario.email == email).first():
                continue
            db.add(Usuario(
                email=email,
                password_hash=hashear_password(password),
                nombre=nombre,
                rol=rol,
            ))
        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    crear_admin_inicial_si_no_existe()
    crear_usuarios_de_prueba_si_no_existen()
    yield


app = FastAPI(
    title="API - Servicio de Autenticación (Gestor de Stock Juguetería)",
    description="Login, JWT de acceso/refresh, roles, y gestión de usuarios.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    # POST /refresh devuelve el nuevo refresh token en este header; sin
    # exponerlo, el navegador no deja que el JS del frontend lo lea (CORS).
    expose_headers=["X-New-Refresh-Token"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": True, "mensaje": "Datos inválidos en la solicitud.", "detalles": exc.errors()},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": True, "mensaje": exc.detail},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": True, "mensaje": "Error interno del servidor."},
    )


app.include_router(auth_router.router)


@app.get("/", tags=["Root"])
def root():
    return {"mensaje": "API Auth - Gestor de Stock Juguetería. Ver /docs."}


@app.get("/health", tags=["Root"])
def health():
    return {"status": "ok", "service": "auth"}
