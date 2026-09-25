# Gestor de Stock — Juguetería

Sistema de gestión de stock para una juguetería. CRUD de productos vía API
REST (FastAPI) con un panel de administración simple (HTML/JS) servido con
nginx.

## Estructura del repositorio

```
.
├── docker-compose.yml
├── .env.example
├── services/
│   ├── productos/        # API REST del CRUD de Producto (FastAPI + SQLite)
│   └── movimientos/       # Placeholder: aún no implementado (ver su README)
└── frontend/              # Panel de administración estático, servido con nginx
```

Cada carpeta bajo `services/` es un servicio independiente, con su propio
Dockerfile y dependencias. `frontend/` es la interfaz web que consume la API
de `productos`.

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

## Variables de entorno

Ver `.env.example` para el detalle de cada variable. Ninguna contiene
secretos: el proyecto no usa credenciales externas por ahora. El archivo
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
`http://localhost:8000` como URL de la API.

## Estado de `services/movimientos`

Todavía no está implementado. Ver `services/movimientos/README.md` para el
alcance previsto y por qué no forma parte de `docker-compose.yml` por ahora.

## Healthchecks

- `productos`: `GET /health` → `{"status": "ok", "service": "productos"}`
- `frontend`: `GET /healthz` → `200 ok` (nginx sirviendo contenido)

`docker compose ps` muestra el estado (`healthy` / `unhealthy`) de cada
servicio una vez que Docker ejecuta los healthchecks definidos en
`docker-compose.yml`.
