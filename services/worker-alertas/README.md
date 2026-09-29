# services/worker-alertas

Consumidor de RabbitMQ que escucha el evento `movimiento.registrado`
(publicado por `services/movimientos`, ver su `EVENTOS.md`) y genera una
alerta cuando el stock resultante de un producto queda por debajo de su
`stock_minimo`.

No expone una API: es un proceso de background (`python worker.py`) que se
queda escuchando la cola. Por eso no tiene healthcheck HTTP en
`docker-compose.yml`; su "salud" se verifica por sus logs
(`docker compose logs -f worker-alertas`).

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
puede llegar más de una vez.

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `RABBITMQ_URL` | `amqp://admin:admin123@localhost:5672/` | Conexión al broker |
| `PRODUCTOS_SERVICE_URL` | `http://localhost:8000` | Para consultar `stock_actual`/`stock_minimo` |
| `ALERTAS_DB_PATH` | `/app/data/alertas_procesadas.db` | Base local de idempotencia |
| `MAX_REINTENTOS` | `5` | Reintentos antes de enviar a la DLQ final |
| `RETRY_TTL_MS` | `5000` | Backoff entre reintentos (fijo; se podría escalonar con más colas de retry) |

## Tests

```bash
cd services/worker-alertas
pip install -r requirements.txt
pytest
```

Los tests (`tests/test_worker_alertas.py`) cubren la idempotencia y la
evaluación de la alerta sin necesitar RabbitMQ ni Productos corriendo (se
inyecta un doble de prueba para `obtener_producto`).
