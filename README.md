# Gestor de Stock — Juguetería

Sistema de gestión de stock para una juguetería. CRUD de productos vía API
REST (FastAPI) con un panel de administración simple (HTML/JS) servido con
nginx.

## Estructura del repositorio

```
.
├── docker-compose.yml
├── .env.example
├── docs/
│   └── ARQUITECTURA.md   # Diagramas (servicios, capas, flujo de baja lógica)
├── services/
│   ├── auth/              # Login, JWT (access/refresh), roles y usuarios (FastAPI + SQLite + Redis)
│   ├── productos/         # API REST del CRUD de Producto (FastAPI + SQLite)
│   └── movimientos/       # Placeholder: aún no implementado (ver su README)
└── frontend/               # Panel de administración estático, servido con nginx
```

Cada carpeta bajo `services/` es un servicio independiente, con su propio
Dockerfile y dependencias. `frontend/` es la interfaz web que consume la API
de `productos`. Ver [`docs/ARQUITECTURA.md`](./docs/ARQUITECTURA.md) para
diagramas de cómo se relacionan estas piezas.

## Requisitos

- Docker y Docker Compose (plugin `compose`, es decir `docker compose`, no el
  binario viejo `docker-compose`).

No se necesita Python ni Node.js instalados localmente: todo corre dentro de
contenedores.

## Puesta en marcha desde cero

```bash
cp .env.example .env
docker compose up --build
```

Esto va a:

0. Levantar Redis y el servicio `auth` (`http://localhost:8001`), que crea los
   usuarios de prueba (ver más abajo), y luego `productos`, que valida los JWT
   emitidos por `auth`.
1. Construir la imagen del servicio `productos` (Python 3.12 + FastAPI) e
   iniciarlo en `http://localhost:8000`, esperando a que su healthcheck
   (`GET /health`) esté en estado saludable.
2. Construir la imagen del `frontend` (nginx) e iniciarlo en
   `http://localhost:8080`, generando en runtime su `config.js` con la URL
   de la API a partir de la variable `PRODUCTOS_API_URL`.

Abrir `http://localhost:8080` en el navegador para usar el panel.

La documentación interactiva de la API (Swagger) queda disponible en
`http://localhost:8000/docs`.

Para bajar todo:

```bash
docker compose down
```

Para bajar todo y borrar también los datos persistidos (empezar 100% limpio):

```bash
docker compose down -v
```

## Usuarios y acceso rápido (entorno de pruebas)

Con `HABILITAR_USUARIOS_PRUEBA=true` (valor por defecto en `.env.example`),
`auth` crea estos dos usuarios al arrancar, y la pantalla de login del
frontend muestra un botón de **acceso rápido** por rol:

| Rol        | Email               | Contraseña     | Permisos                                                      |
|------------|---------------------|----------------|---------------------------------------------------------------|
| `admin`    | `admin@test.com`    | `admin12345`   | Todo: productos (incluido eliminar) y gestión de usuarios.     |
| `empleado` | `empleado@test.com` | `empleado123`  | Ver/crear/editar productos. No puede eliminar ni gestionar usuarios. |

Los valores salen de `PRUEBA_ADMIN_*` y `PRUEBA_EMPLEADO_*` en `.env`. Para
un entorno real, poner `HABILITAR_USUARIOS_PRUEBA=false`: no se crean los
usuarios y el frontend deja de recibir las credenciales. Opcionalmente se
puede definir un admin propio con `AUTH_ADMIN_EMAIL` / `AUTH_ADMIN_PASSWORD`
(se crea solo si la base de usuarios está vacía).

> Si cambiás las contraseñas de prueba en `.env` con una base ya creada, el
> usuario existente conserva la anterior: `docker compose down -v` para
> empezar de cero.

## Variables de entorno

Ver `.env.example` para el detalle de cada variable. `AUTH_SECRET_KEY` (firma
de los JWT) es obligatoria; el valor de `.env.example` es solo para desarrollo. El archivo
`.env` real (con los valores que cada quien use localmente) **no se sube al
repositorio** (ver `.gitignore`).

## Persistencia de datos

El servicio `productos` guarda su base SQLite en el volumen Docker
`productos_data`, montado en `/app/data` dentro del contenedor. El archivo
`jugueteria.db` **no forma parte del repositorio**: se genera solo la primera
vez que arranca el contenedor y vive únicamente en ese volumen.

Para precargar datos de ejemplo una vez que el servicio está corriendo:

```bash
docker compose exec productos python seed_data.py
```

## Desarrollo local sin Docker (opcional)

**Windows, sin instalar Redis:** `.\ejecutar_local.ps1` (desde PowerShell, en
la raíz del proyecto) crea los entornos virtuales, instala dependencias y
levanta `auth`, `productos` y el frontend, usando un Redis en memoria
(`REDIS_URL=memory://`, vía `fakeredis`). Solo para probar: el logout no
invalida el token en `productos`. Abre `http://localhost:8080`.

Alternativa manual (requiere un Redis real):

Si se prefiere correr el backend directamente con Python:

```bash
cd services/productos
python -m venv venv
. venv/bin/activate        # En Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Por defecto usa `sqlite:///./data/jugueteria.db` dentro de esa misma carpeta
(también ignorado por Git). Para abrir el frontend en este modo, basta con
abrir `frontend/index.html` directamente en el navegador: sin `config.js`
generado por Docker, `app.js` cae automáticamente a
`http://localhost:8000` como URL de la API (y `http://localhost:8001` para
auth). En ese modo no hay `config.js`, así que los botones de acceso rápido no
aparecen: hay que iniciar sesión con email y contraseña, y `auth` + Redis
tienen que estar corriendo.

## Estado de `services/movimientos`

Todavía no está implementado. Ver `services/movimientos/README.md` para el
alcance previsto y por qué no forma parte de `docker-compose.yml` por ahora.

## Contrato de la API

El contrato OpenAPI del servicio `productos` está exportado en
[`services/productos/openapi.json`](./services/productos/openapi.json).
Regenerarlo tras cualquier cambio de schemas o rutas:

```bash
cd services/productos
python export_openapi.py
```

También está disponible en vivo mientras el servicio corre, en
`http://localhost:8000/docs` (Swagger UI) y `http://localhost:8000/openapi.json`.

## Tests

```bash
cd services/productos
pip install -r requirements-dev.txt
pytest
```

La suite (38 tests) cubre CRUD, validaciones de entrada, la baja lógica de
productos y flujos de integración de punta a punta. Corre contra una base
SQLite en memoria aislada por test — no requiere Docker ni toca la base de
datos real. Ver el detalle de qué cubre cada archivo en
[`services/productos/README.md`](./services/productos/README.md#tests).

## Calidad y decisiones de diseño

Dos correcciones de diseño relevantes en el servicio `productos` (ver el
detalle completo, con la justificación y los tests que las respaldan, en
[`services/productos/README.md`](./services/productos/README.md#decisiones-de-diseño-issue-6)):

- **El PUT de productos ya no puede pisar `stock_actual`.** El schema de
  actualización no incluye ese campo: el stock se modifica exclusivamente
  vía movimientos de inventario, nunca editando la ficha del producto.
- **Baja lógica en vez de borrado físico** cuando un producto tiene
  movimientos de stock asociados (se marca `activo=false` en lugar de
  eliminarse), para no perder la trazabilidad histórica. Ver el diagrama de
  flujo en [`docs/ARQUITECTURA.md`](./docs/ARQUITECTURA.md#flujo-de-delete-apiv1productosid-issue-6).

Las limitaciones conocidas del sistema (falta de autenticación, ausencia de
CI, alcance de SQLite, etc.) están documentadas en
[`docs/ARQUITECTURA.md`](./docs/ARQUITECTURA.md#limitaciones-del-sistema-resumen)
y, con más detalle por servicio, en el README de `services/productos`.

## Healthchecks

- `productos`: `GET /health` → `{"status": "ok", "service": "productos"}`
- `frontend`: `GET /healthz` → `200 ok` (nginx sirviendo contenido)

`docker compose ps` muestra el estado (`healthy` / `unhealthy`) de cada
servicio una vez que Docker ejecuta los healthchecks definidos en
`docker-compose.yml`.
