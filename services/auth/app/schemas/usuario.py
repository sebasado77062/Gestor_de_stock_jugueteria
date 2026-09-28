"""
Schemas del servicio auth.
"""
from pydantic import BaseModel, EmailStr, Field, ConfigDict

from app.models.usuario import RolUsuario


class UsuarioCreate(BaseModel):
    """Alta de un usuario nuevo. Solo la puede hacer un admin (ver router)."""
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72, description="Mínimo 8 caracteres.")
    nombre: str = Field(..., min_length=1, max_length=120)
    rol: RolUsuario = RolUsuario.EMPLEADO


class UsuarioOut(BaseModel):
    id_usuario: int
    email: EmailStr
    nombre: str
    rol: RolUsuario
    activo: bool

    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
