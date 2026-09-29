# Catálogo de eventos — services/movimientos

## `movimiento.registrado`

| Campo | Valor |
|---|---|
| **Exchange** | `stock.events` (tipo `topic`, durable) |
| **Routing key** | `movimiento.registrado` |
| **Productor** | `services/movimientos` (`app/messaging/publisher.py`), al finalizar un `POST /api/v1/movimientos` exitoso |
| **Consumidores actuales** | `services/worker-alertas` (cola `alertas.stock.bajo`) |
| **Entrega** | *At-least-once* (RabbitMQ puede reentregar el mismo mensaje ante fallos de red o reinicios del consumidor). Por eso el consumidor debe ser **idempotente** — ver más abajo. |
| **Persistencia** | Mensaje publicado con `delivery_mode=2` (persistente) hacia una cola durable, para sobrevivir a un reinicio del broker. |

### Payload (JSON)

```json
{
  "id_movimiento": 123,
  "id_producto": 45,
  "categoria_movimiento": "venta",
  "cantidad_movida": 2,
  "fecha_hora": "2026-09-27T14:32:10.123456+00:00"
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `id_movimiento` | int | Identificador único del movimiento. Es la clave de idempotencia usada por los consumidores. |
| `id_producto` | int | Producto afectado (referencia al servicio de Productos). |
| `categoria_movimiento` | string | `venta` \| `ingreso` \| `estropeo` \| `devolucion` |
| `cantidad_movida` | int | Cantidad movida (siempre positiva; el signo del ajuste de stock ya se aplicó en Productos antes de publicar el evento). |
| `fecha_hora` | string (ISO 8601) | Momento en que se registró el movimiento. |

### Idempotencia del consumidor

El worker de Alertas identifica cada mensaje por `id_movimiento` y verifica,
antes de procesarlo, si ya generó una alerta para ese movimiento (registro
local en `alertas_procesadas.db`). Si ya fue procesado, confirma el mensaje
(`ack`) sin reprocesar. Esto es necesario porque RabbitMQ garantiza
*at-least-once*, no *exactly-once*: el mismo mensaje puede llegar más de una
vez (por ejemplo, si el worker se cae después de procesar pero antes de
confirmar).

### Reintentos y Dead-Letter Queue (DLQ)

Ver `services/worker-alertas/README.md` para el detalle de la topología de
colas (`alertas.stock.bajo` → `alertas.stock.bajo.retry` → vuelve a
`alertas.stock.bajo`, hasta un máximo de reintentos; después de eso, el
mensaje se deriva a `alertas.stock.bajo.dlq` para inspección manual).

### Evolución futura

Nuevos eventos de este dominio (por ejemplo, `movimiento.anulado`) se
publicarían sobre el mismo exchange `stock.events` con una routing key
distinta, sin necesidad de modificar a los consumidores existentes que no
estén interesados en ellos (ventaja del exchange `topic`).
