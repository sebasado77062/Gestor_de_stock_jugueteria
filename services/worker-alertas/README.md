# services/worker-alertas

Consumidor de RabbitMQ que escucha el evento `movimiento.registrado`
(publicado por `services/movimientos`, ver su `EVENTOS.md`) y genera una
alerta cuando el stock resultante de un producto queda **en su `stock_minimo`
o por debajo** (`stock_actual <= stock_minimo`). Es la misma regla que usa la
tabla de Inventario del frontend, para que una pantalla y la alerta nunca
discrepan; con `stock_minimo = 0` también alerta cuando el producto se agota.

No expone una API de negocio: es un proceso de background (`python worker.py`)
que se queda escuchando la cola. Sí expone un **healthcheck HTTP** mínimo
(`GET /health`, puerto 8003, solo biblioteca estándar; ver `health.py`):

| Respuesta | Significa |
|---|---|
| `200 {"status": "ok"}` | Conectado a RabbitMQ, consumiendo y con latido reciente |
| `503 {"status": "starting"}` | Todavía conectando al broker |
| `503 {"status": "unhealthy"}` | Dejó de latir (consumidor trabado) |

El "latido" lo ejecuta la propia conexión de RabbitMQ cada
`WORKER_HEARTBEAT_SEGUNDOS` dentro del mismo loop que procesa los mensajes: si
ese loop se traba, el latido se detiene y el healthcheck pasa a 503. Un simple
"el puerto responde" no lo detectaría. Docker Compose lo usa como healthcheck
(con Python, porque la imagen no trae `curl`), así que `docker compose ps` lo
muestra como `(healthy)`. Si se corta la conexión con RabbitMQ el proceso
termina y Docker lo reinicia (`restart: unless-stopped`). Los logs siguen
disponibles: `docker compose logs -f worker-alertas`.

## Evento consumido

El evento `movimiento.registrado` es **autocontenido**: incluye un snapshot
del producto (`stock_actual`, `stock_minimo`, `nombre_producto`) al momento
del ajuste, junto con los datos del movimiento. Por eso el worker **no
necesita consultar a `productos`** para evaluar la alerta: toda la
información que necesita viene en el propio payload.

Esto es una decisión de diseño deliberada. La alternativa (que el worker
llamara a `productos` por HTTP) habría requerido que el worker tuviera un
JWT de usuario válido, pero un consumidor asíncrono no tiene uno. Incluir el
snapshot en el evento elimina esa dependencia, reduce el acoplamiento y
hace al worker más simple (una sola conexión: RabbitMQ).

Ver `services/movimientos/EVENTOS.md` para el catálogo completo del evento
(exchange, routing key, payload, garantías de entrega).

## Topología de colas

```
stock.events (exchange topic)
      │ routing key: movimiento.registrado
      ▼
alertas.stock.bajo  ──(falla, nack)──►  stock.events.dlx (exchange direct)
      ▲                                        │ routing key: alertas.stock.bajo.retry
      │ vuelve tras el TTL                     ▼
      └──────────────────────────  alertas.stock.bajo.retry (cola con TTL = backoff)

Tras MAX_REINTENTOS fallidos ──► alertas.stock.bajo.dlq (inspección manual)
```

- **Backoff**: la cola `alertas.stock.bajo.retry` no tiene consumidor
  propio; solo retiene el mensaje `RETRY_TTL_MS` (5000 ms por defecto) y
  luego lo dead-letterea de vuelta a `alertas.stock.bajo`, reintentando el
  procesamiento.
- **Máximo de reintentos**: `MAX_REINTENTOS` (5 por defecto). Se cuenta a
  partir del header automático `x-death` que RabbitMQ agrega cada vez que
  un mensaje es dead-letterado.
- **DLQ final**: `alertas.stock.bajo.dlq`, para mensajes que agotaron los
  reintentos. Se puede inspeccionar desde el panel de RabbitMQ
  (`http://localhost:15672`).

## Idempotencia

Cada mensaje se identifica por `id_movimiento`. Antes de procesar, el
worker consulta una base SQLite local (`alertas_procesadas.db`, montada
como volumen) para saber si ya generó (o descartó) una alerta para ese
movimiento. Si ya fue procesado, confirma el mensaje sin reprocesar. Esto
es necesario porque RabbitMQ entrega *at-least-once*: el mismo mensaje
puede llegar más de una vez (por ejemplo, si el worker se cae después de
procesar pero antes de confirmar).

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `RABBITMQ_URL` | `amqp://admin:admin123@localhost:5672/` | Conexión al broker |
| `ALERTAS_DB_PATH` | `/app/data/alertas_procesadas.db` | Base local de idempotencia |
| `WORKER_HEALTH_PORT` | `8003` | Puerto del healthcheck HTTP |
| `WORKER_HEARTBEAT_SEGUNDOS` | `10` | Cada cuánto late el consumidor; pasa a `unhealthy` tras 3 latidos perdidos |
| `MAX_REINTENTOS` | `5` | Reintentos antes de enviar a la DLQ final |
| `RETRY_TTL_MS` | `5000` | Backoff entre reintentos (fijo; se podría escalonar con más colas de retry) |

Nota: ya **no** se usa `PRODUCTOS_SERVICE_URL`, porque el worker no
consulta Productos (el evento es autocontenido, ver arriba).

## Tests

```bash
cd services/worker-alertas
pip install -r requirements.txt
pytest
```

Los tests (`tests/test_worker_alertas.py`) cubren:
- **Idempotencia**: un `id_movimiento` ya procesado no se vuelve a
  procesar; el registro es idempotente por sí mismo (INSERT OR IGNORE).
- **Evaluación de la alerta**: se dispara cuando `stock_actual <=
  stock_minimo` en el payload (incluido el caso exacto en el mínimo y el de
  mínimo 0 con producto agotado), y no se dispara cuando hay stock suficiente.
- **Conteo de reintentos**: parseo del header `x-death` que RabbitMQ
  agrega en cada dead-lettering.

No requieren RabbitMQ ni Productos corriendo: el payload del evento se
construye directamente en cada test (ya trae el snapshot del producto).

`tests/test_health.py` cubre el healthcheck: la lógica de estados con un reloj
controlado (starting / ok / unhealthy cuando se detiene el latido / recuperación)
y el servidor HTTP real (200, 503, 404 y puerto ocupado sin romper al worker).
Tampoco requiere RabbitMQ.
