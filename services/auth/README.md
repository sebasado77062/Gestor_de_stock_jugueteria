# services/auth

API REST de autenticación y autorización del sistema. Emite y revoca JWT
(access + refresh), gestiona usuarios y expone el endpoint `/me` con el que
el frontend obtiene los datos del usuario logueado. Construida con FastAPI +
SQLAlchemy + Redis.

## Ejecutar

Ver el README raíz del repositorio para levantar todo con Docker Compose.
Para correr solo este servicio en desarrollo:

```bash
cd services/auth
python -m venv venv && . venv/bin/activate
pip install -r requirements.txt
export AUTH_SECRET_KEY=clave-de-desarrollo
uvicorn app.main:app --reload --port 8001
```

Requiere Redis corriendo (o `REDIS_URL=memory://` para usar `fakeredis` en
desarrollo local, ver `requirements-local.txt`). Documentación interactiva:
`http://localhost:8001/docs`.

## Endpoints

Prefijo: `/api/v1/auth`.

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/login` | Autentica email + contraseña y devuelve un par de tokens |
| `POST` | `/refresh` | Renueva el access token a partir de un refresh token |
| `POST` | `/logout` | Revoca los tokens actuales (agrega el `jti` a una blacklist en Redis) |
| `GET` | `/me` | Datos del usuario logueado (requiere access token válido) |
| `GET` | `/health` | Healthcheck |

### Login

```bash
curl -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@test.com", "password": "admin12345"}'
```

Respuesta:

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer"
}
```

### Refresh

El refresh token es **de un solo uso**: cada llamada a `/refresh` devuelve un
refresh token nuevo en el header de respuesta `X-New-Refresh-Token`. Si el
cliente lo pierde y reutiliza el anterior, ese refresh queda invalidado.

```bash
curl -X POST http://localhost:8001/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "<token>"}'
```

### Logout

Agrega el `jti` del access token a la blacklist de Redis
(`auth:blacklist:<jti>`), con TTL igual al tiempo de vida restante del
token. A partir de ese momento, cualquier servicio que valide JWT (auth,
productos, movimientos) rechaza ese token aunque su firma siga siendo
válida y no haya expirado.

## Autenticación entre servicios

`auth` es el **único** servicio que firma y revoca JWT. Los demás servicios
(`productos`, `movimientos`) validan el token **localmente**, compartiendo
la misma `AUTH_SECRET_KEY` por variable de entorno. La única dependencia de
red es Redis, para consultar la blacklist de revocados:

```
auth    ──(firma JWT con AUTH_SECRET_KEY)──►  cliente
cliente ──(Bearer <token>)──►  productos / movimientos
                                  │
                                  ├──(valida firma/expiración localmente)
                                  └──(consulta blacklist en Redis)
```

Si Redis está caído, `productos` y `movimientos` fallan hacia "no revocado"
(fail-open): siguen validando firma y expiración, pero pierden la capacidad
de rechazar tokens revocados antes de que expiren solos.

## Roles

| Rol | Descripción |
|---|---|
| `admin` | Acceso total: CRUD de productos (incluido eliminar) y gestión de usuarios |
| `empleado` | Listar, ver, crear y actualizar productos y registrar movimientos. No puede eliminar. |

El rol viaja en el payload del JWT (`rol`) y cada servicio lo verifica en
sus propios endpoints según sus propias reglas.

## Usuarios de prueba

Con `HABILITAR_USUARIOS_PRUEBA=true` en el `.env` (valor por defecto), el
servicio crea dos usuarios al arrancar, si la base está vacía:

| Rol | Email | Contraseña |
|---|---|---|
| `admin` | `admin@test.com` | `admin12345` |
| `empleado` | `empleado@test.com` | `empleado123` |

Los valores salen de las variables `PRUEBA_ADMIN_*` y `PRUEBA_EMPLEADO_*`.
Opcionalmente se puede definir un admin propio con `AUTH_ADMIN_EMAIL` /
`AUTH_ADMIN_PASSWORD` (se crea solo si la base de usuarios está vacía).

Para entornos reales, poner `HABILITAR_USUARIOS_PRUEBA=false`: no se crean
los usuarios y el frontend deja de recibir las credenciales.

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/auth.db` | Base propia de Auth |
| `AUTH_SECRET_KEY` | — (obligatoria) | Secreto compartido con los demás servicios para firmar/validar JWT |
| `REDIS_URL` | `redis://localhost:6379/0` | Blacklist de tokens revocados. `memory://` para modo local |
| `HABILITAR_USUARIOS_PRUEBA` | `true` | Crea los usuarios de prueba al arrancar (solo desarrollo) |
| `PRUEBA_ADMIN_EMAIL` / `PRUEBA_ADMIN_PASSWORD` | `admin@test.com` / `admin12345` | Credenciales del admin de prueba |
| `PRUEBA_EMPLEADO_EMAIL` / `PRUEBA_EMPLEADO_PASSWORD` | `empleado@test.com` / `empleado123` | Credenciales del empleado de prueba |
| `AUTH_ADMIN_EMAIL` / `AUTH_ADMIN_PASSWORD` | — | Admin propio, se crea solo si la base está vacía |

## Persistencia

SQLite mediante SQLAlchemy, en su propio volumen Docker (`auth_data`). Base
**independiente** de `productos` y `movimientos`: cada servicio es dueño de
sus datos, y la comunicación entre ellos es vía API REST, nunca compartiendo
tablas. La blacklist de tokens revocados vive en Redis, que sí se comparte
con los otros servicios (es el único estado distribuido necesario).

## Tests

```bash
cd services/auth
pip install -r requirements-dev.txt
pytest
```

Los tests cubren:
- **Login** (`test_login.py`): credenciales válidas/inválidas, usuario
  inactivo, formato de la respuesta.
- **Logout** (`test_logout.py`): el token queda en la blacklist y se
  rechaza en requests posteriores.
- **Refresh** (`test_refresh.py`): rotación del refresh token (el anterior
  deja de funcionar), refresh inválido o expirado.
- **Roles** (`test_roles.py`): cada endpoint respeta los roles permitidos.

Los tests corren contra una base SQLite en memoria y un Redis en memoria
(`fakeredis`), aislados entre sí.

## Contrato de la API

El contrato OpenAPI está exportado en [`openapi.json`](./openapi.json)
(regenerarlo con `python export_openapi.py` tras cualquier cambio de
schemas o rutas) y también disponible en vivo en `/docs` y `/openapi.json`
mientras el servicio corre.