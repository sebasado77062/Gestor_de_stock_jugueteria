# Catálogo de eventos — services/movimientos

## `movimiento.registrado`

| Campo | Valor |
|---|---|
| **Exchange** | `stock.events` (tipo `topic`, durable) |
| **Routing key** | `movimiento.registrado` |
| **Productor** | `services/movimientos`. El evento se guarda en el **outbox** (tabla `outbox_eventos`) al *confirmar* el movimiento y lo publica el relay (`app/messaging/outbox.py` + `publisher.py`). No se publica nada de un movimiento `pendiente` ni de uno descartado. |
| **Consumidores actuales** | `services/worker-alertas` (cola `alertas.stock.bajo`) |
| **Entrega** | *At-least-once* de punta a punta: el relay puede publicar un evento más de una vez (si el proceso cae entre publicar y marcarlo como publicado) y RabbitMQ puede reentregar ante fallos de red o reinicios del consumidor. Por eso el consumidor debe ser **idempotente** — ver más abajo. |
| **Persistencia** | Mensaje publicado con `delivery_mode=2` (persistente) hacia una cola durable, para sobrevivir a un reinicio del broker. |
| **Confiabilidad al publicar** | Ver "Publicación confiable (outbox)" más abajo: el evento no se pierde si RabbitMQ está caído. |

### Publicación confiable (outbox)

Publicar directamente desde el request tiene un hueco: si RabbitMQ está caído
justo después de confirmar el movimiento, el evento se pierde. Con el outbox:

1. Al confirmar el movimiento, el evento se inserta en `outbox_eventos` **en la
   misma transacción** que cambia el estado a `confirmado`: quedan los dos o
   ninguno.
2. Un relay en segundo plano (hilo de `movimientos`) toma los eventos sin
   publicar (`publicado_en` vacío), en orden, y los envía con *publisher
   confirms*. Solo cuando el broker confirma los marca como publicados.
3. Si RabbitMQ no responde, el evento sigue pendiente (se anota el error en
   `ultimo_error` y se incrementa `intentos`) y se reintenta con backoff
   (desde `OUTBOX_INTERVALO_SEGUNDOS`, por defecto 5 s, hasta 60 s). Sobrevive a
   un reinicio de `movimientos` porque está en su base de datos.
4. Se publica con `mandatory=true`: si ninguna cola está enlazada al exchange
   (por ejemplo, el worker todavía no declaró `alertas.stock.bajo`), RabbitMQ
   devuelve el mensaje y el evento queda pendiente en lugar de descartarse en
   silencio. Se desactiva con `RABBITMQ_EXIGIR_RUTEO=false`.

Para ver el estado: `select id, publicado_en, intentos, ultimo_error from
outbox_eventos;` en `movimientos.db`.

### Diseño del evento: autocontenido

El evento incluye un **snapshot del producto** (`stock_actual`,
`stock_minimo`, `nombre_producto`) al momento del ajuste, además de los
datos del movimiento. El objetivo es que los consumidores no necesiten
consultar a `productos` por HTTP para evaluar la regla de negocio
("¿el stock quedó en el mínimo o por debajo?", es decir `stock_actual <=
stock_minimo`, la misma regla que la tabla de Inventario del frontend).

**Por qué:** un consumidor asíncrono (como el worker de alertas) no tiene
un JWT de usuario con el cual llamar a `productos`, que exige
autenticación. Alternativas como un token de servicio o un endpoint
interno sin auth habrían agregado complejidad y acoplamiento entre
servicios. El evento autocontenido resuelve el problema sin dependencias
adicionales.

**Contrapartida:** el evento refleja el estado del producto *al momento
del movimiento*, no el estado actual. Si dos movimientos se publican en
rápida sucesión, un consumidor que procese el segundo verá el stock
resultante después del segundo ajuste — que es lo correcto para una
alerta. Si en algún momento se necesita el estado "en vivo" del producto,
sería un cambio de diseño (nuevo evento o API dedicada), no un ajuste
trivial del payload.

### Payload (JSON)

```json
{
  "id_movimiento": 123,
  "id_producto": 45,
  "categoria_movimiento": "venta",
  "cantidad_movida": 2,
  "fecha_hora": "2026-09-27T14:32:10.123456+00:00",
  "stock_actual": 3,
  "stock_minimo": 5,
  "nombre_producto": "Auto a fricción"
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `id_movimiento` | int | Identificador único del movimiento. Es la clave de idempotencia usada por los consumidores. |
| `id_producto` | int | Producto afectado (referencia al servicio de Productos). |
| `categoria_movimiento` | string | `venta` \| `ingreso` \| `estropeo` \| `devolucion` |
| `cantidad_movida` | int | Cantidad movida (siempre positiva; el signo del ajuste de stock ya se aplicó en Productos antes de confirmar el movimiento y publicar el evento). |
| `fecha_hora` | string (ISO 8601) | Momento en que se registró el movimiento. |
| `stock_actual` | int | Stock del producto **después** del ajuste aplicado por este movimiento. (En un movimiento que se confirmó por reconciliación es el stock *actual* del producto en ese momento.) |
| `stock_minimo` | int | Stock mínimo configurado del producto. |
| `nombre_producto` | string | Nombre del producto (para que el consumidor pueda loguear/mostrar sin consultar `productos`). |

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