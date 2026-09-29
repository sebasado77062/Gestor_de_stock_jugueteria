# services/movimientos

API REST para el registro de movimientos de stock (venta, ingreso,
estropeo, devolución). Es un registro histórico de **solo inserciones**: no
hay endpoints de edición ni borrado de un movimiento (ver
`app/models/movimiento.py`).

Cada movimiento ajusta el `stock_actual` del producto correspondiente
llamando al servicio `productos` vía API REST (nunca accede directamente a
su base de datos), usando su endpoint atómico
`PATCH /api/v1/productos/{id}/ajustar-stock` para evitar condiciones de
carrera ante ventas concurrentes (ver `services/productos/tests/test_concurrencia_stock.py`).

También publica el evento `movimiento.registrado` en RabbitMQ (exchange
`stock.events`), consumido por `services/worker-alertas` para evaluar si
corresponde una alerta de stock bajo. Ver `EVENTOS.md` para el catálogo
completo.

## Ejecutar

Ver el README raíz del repositorio para levantar todo con Docker Compose.
Para correr solo este servicio en desarrollo:

```bash
cd services/movimientos
python -m venv venv && . venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8002
```

Requiere que `services/productos` esté corriendo (por defecto en
`http://localhost:8000`). Redis y RabbitMQ son opcionales para el flujo
básico: si no están disponibles, el servicio sigue registrando movimientos
igual, solo se pierden la idempotencia y la publicación del evento (ver
manejo de errores en `app/routers/movimiento_router.py`).

Documentación interactiva: `http://localhost:8002/docs`.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/api/v1/movimientos` | Registra un movimiento y ajusta el stock en Productos |
| `GET` | `/api/v1/movimientos` | Lista todos los movimientos (más reciente primero) |
| `GET` | `/api/v1/productos/{id}/movimientos` | Lista los movimientos de un producto puntual |
| `GET` | `/health` | Healthcheck |
| `GET` | `/docs` | Documentación interactiva (OpenAPI / Swagger) |

**Autenticación:** todos los endpoints exigen un usuario autenticado (JWT
válido, emitido por `services/auth`). El token del usuario se propaga a
`productos` al ajustar el stock, para que sea `productos` quien aplique sus
propias reglas de rol sobre ese usuario (no se usan credenciales de
servicio).

El contrato completo está exportado en [`openapi.json`](./openapi.json)
(regenerarlo con `python export_openapi.py` tras cualquier cambio de
schemas o rutas) y también disponible en vivo en `/docs` y `/openapi.json`.

### Categorías de movimiento

| Categoría | Efecto sobre el stock |
|---|---|
| `venta` | Descuenta |
| `estropeo` | Descuenta |
| `ingreso` | Incrementa |
| `devolucion` | Incrementa |

### Idempotencia (`Idempotency-Key`)

`POST /api/v1/movimientos` acepta un header opcional `Idempotency-Key`. Si
se reenvía la misma solicitud con la misma clave (por ejemplo, ante un
timeout o un doble clic en la UI), no se vuelve a ajustar el stock ni se
crea un movimiento duplicado: se devuelve el mismo movimiento registrado la
primera vez. La clave se guarda en Redis con un TTL de 24 h.

```bash
curl -X POST http://localhost:8002/api/v1/movimientos \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -H "Idempotency-Key: 7f3a2b10-2b6b-4b8a-9c3f-1a2b3c4d5e6f" \
  -d '{"categoria_movimiento":"venta","cantidad_movida":1,"id_producto":1}'
```

Si Redis está caído, el servicio sigue funcionando pero pierde la garantía
de idempotencia en ese request puntual (se loguea la excepción y se
continúa). Es una degradación controlada: la disponibilidad del registro de
movimientos es más importante que la garantía de idempotencia.

### Evento publicado

Al registrar un movimiento, se publica el evento `movimiento.registrado` en
RabbitMQ (exchange `stock.events`). El evento es **autocontenido**: incluye
un snapshot del producto (`stock_actual`, `stock_minimo`, `nombre`) al
momento del ajuste, para que los consumidores no tengan que consultar a
`productos` (que exige JWT de usuario). Ver `EVENTOS.md` para el catálogo
completo.

Una falla al publicar el evento (RabbitMQ caído, por ejemplo) **no** impide
que `POST /api/v1/movimientos` responda `201 Created`: el movimiento y el
ajuste de stock ya quedaron confirmados; solo se pierde, en ese caso
puntual, la notificación asíncrona.

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/movimientos.db` | Base propia de Movimientos |
| `PRODUCTOS_SERVICE_URL` | `http://localhost:8000` | URL interna del servicio Productos |
| `PRODUCTOS_SERVICE_TIMEOUT` | `5` (segundos) | Timeout de las llamadas HTTP a Productos |
| `AUTH_SECRET_KEY` | — (obligatoria) | Mismo secreto que `auth` y `productos`, para validar JWT |
| `REDIS_URL` | `redis://localhost:6379/0` | Para la idempotencia de `POST /movimientos` |
| `IDEMPOTENCY_TTL_SEGUNDOS` | `86400` (24 h) | TTL de cada Idempotency-Key en Redis |
| `RABBITMQ_URL` | `amqp://admin:admin123@localhost:5672/` | Para publicar `movimiento.registrado` |

## Persistencia

SQLite mediante SQLAlchemy, en su propio volumen Docker
(`movimientos_data`). Es una base **independiente** de la de `productos`:
cada servicio es dueño de sus datos, y la comunicación entre ellos es
siempre por API REST o eventos, nunca compartiendo tablas.

## Tests

```bash
cd services/movimientos
pip install -r requirements.txt
pytest
```

Los tests no requieren Productos, Redis ni RabbitMQ corriendo: se mockean
(`unittest.mock.patch`) el cliente HTTP, el publicador de eventos, y las
operaciones de Redis.

Cubren:
- **Delta por categoría** (`test_movimientos.py`): el signo del ajuste
  depende de la categoría (venta/estropeo descuentan, ingreso/devolución
  incrementan).
- **Propagación de errores**: si Productos devuelve 409 (sin stock), no
  queda un movimiento huérfano.
- **Idempotencia** (`test_idempotencia.py`): dos requests con la misma
  `Idempotency-Key` no duplican el movimiento ni ajustan el stock dos veces.