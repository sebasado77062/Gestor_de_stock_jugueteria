# services/worker-alertas

Consumidor de RabbitMQ que escucha el evento `movimiento.registrado`
(publicado por `services/movimientos`, ver su `EVENTOS.md`) y genera una
alerta cuando el stock resultante de un producto queda por debajo de su
`stock_minimo`.

No expone una API: es un proceso de background (`python worker.py`) que se
queda escuchando la cola. Por eso no tiene healthcheck HTTP en
`docker-compose.yml`; su "salud" se verifica por sus logs
(`docker compose logs -f worker-alertas`). `docker compose ps` va a
mostrarlo como `Up` sin `(healthy)`, y eso es esperado.

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
- **Evaluación de la alerta**: se dispara cuando `stock_actual <
  stock_minimo` en el payload, y no se dispara cuando hay stock suficiente.
- **Conteo de reintentos**: parseo del header `x-death` que RabbitMQ
  agrega en cada dead-lettering.

No requieren RabbitMQ ni Productos corriendo: el payload del evento se
construye directamente en cada test (ya trae el snapshot del producto).