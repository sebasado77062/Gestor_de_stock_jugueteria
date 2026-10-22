# services/movimientos

API REST para el registro de movimientos de stock (venta, ingreso,
estropeo, devolución). Es un registro histórico de **solo inserciones**: no
hay endpoints de edición ni borrado de un movimiento (ver
`app/models/movimiento.py`).

Cada movimiento ajusta el `stock_actual` del producto correspondiente
llamando al servicio `productos` vía API REST (nunca accede directamente a
su base de datos), usando su endpoint atómico e idempotente
`PATCH /api/v1/productos/{id}/ajustar-stock` para evitar condiciones de
carrera ante ventas concurrentes (ver `services/productos/tests/test_concurrencia_stock.py`).

Como el stock (en `productos`) y el historial (acá) viven en bases distintas,
el registro es una **saga en tres pasos** (ver "Consistencia: saga de registro"
más abajo): guarda el movimiento como `pendiente`, ajusta el stock y lo
confirma. Si algo falla en el medio, queda `pendiente` y se completa con un
reintento o una reconciliación.

También publica el evento `movimiento.registrado` en RabbitMQ (exchange
`stock.events`) a través de un **outbox transaccional**: el evento se guarda
en la misma transacción que confirma el movimiento y un relay lo publica, por
lo que no se pierde aunque RabbitMQ esté caído. Lo consume
`services/worker-alertas` para evaluar si corresponde una alerta de stock bajo.
Ver `EVENTOS.md` para el catálogo completo.

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
`http://localhost:8000`). RabbitMQ es opcional para el flujo básico: si no
está disponible, el servicio sigue registrando movimientos y sus eventos
esperan en el outbox hasta que el broker vuelva. Redis solo se usa para la
blacklist de tokens revocados (logout); la idempotencia ya no depende de él.

Al arrancar, el servicio migra solo el esquema de una base ya existente
(agrega las columnas `estado`, `operacion_id` e `idempotency_key`; los
movimientos anteriores quedan como `confirmado`), así que no hace falta
borrar el volumen al actualizar.

Documentación interactiva: `http://localhost:8002/docs`.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/api/v1/movimientos` | Registra un movimiento y ajusta el stock en Productos (acepta `Idempotency-Key`) |
| `POST` | `/api/v1/movimientos/reconciliar` | **Solo admin.** Resuelve los movimientos `pendiente`: confirma los que sí se aplicaron en Productos y descarta los que no. Nunca modifica el stock. Parámetro `antiguedad_minima_segundos` (default 300) |
| `GET` | `/api/v1/movimientos` | Lista todos los movimientos, confirmados y pendientes (más reciente primero) |
| `GET` | `/api/v1/productos/{id}/movimientos` | Lista los movimientos de un producto puntual |
| `GET` | `/api/v1/productos/{id}/movimientos/cantidad` | Cantidad de movimientos de un producto (la usa Productos antes de dar de baja) |
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

### Consistencia: saga de registro

Estados de un movimiento (columna `estado`):

- `pendiente`: la intención está guardada pero todavía no se confirmó que el
  stock se haya ajustado en Productos.
- `confirmado`: el stock se ajustó y el movimiento es parte del historial.

El registro (`app/services/movimiento_service.py`):

1. **Guarda la intención** (`pendiente`, con una `operacion_id` propia).
2. **Ajusta el stock** en Productos enviando `operacion_id` como
   `Idempotency-Key`. Productos lo aplica una sola vez por clave.
3. **Confirma**, en una única transacción local: estado `confirmado` + evento
   en el outbox.

| Si falla… | Resultado |
|---|---|
| Productos responde 404 / 409 | El stock no se tocó: se descarta la intención. No queda nada. |
| Productos no responde, o se pierde su respuesta | El movimiento queda `pendiente`; el cliente recibe 503 y un mensaje que lo aclara. Reintentar con la misma clave lo completa. |
| El guardado del paso 3 | Stock ya ajustado y movimiento `pendiente`: el reintento lo confirma (no se "deshace" el stock). |

`POST /api/v1/movimientos/reconciliar` (admin) resuelve los pendientes que
nadie reintentó: consulta a Productos (`GET /api/v1/productos/ajustes/{clave}`)
si cada operación se aplicó; la confirma si sí y la descarta si no. **Nunca
vuelve a ajustar el stock**: una intención abandonada no se ejecuta por
sorpresa. Solo mira pendientes con al menos `antiguedad_minima_segundos`
(300 por defecto) para no pisar un reintento en curso. Limitaciones: ver el
README raíz, "Limitaciones conocidas".

Un 404/409 de Productos descarta la intención, pero un 401/403 **no**: puede
tratarse del reintento de una operación ya aplicada, así que el movimiento queda
`pendiente`.

### Idempotencia (`Idempotency-Key`)

`POST /api/v1/movimientos` acepta un header opcional `Idempotency-Key`. Si el
mismo pedido se repite (timeout, doble clic, reintento del frontend), se
devuelve el mismo movimiento sin volver a ajustar el stock; si estaba
`pendiente`, se completa.

```bash
curl -X POST http://localhost:8002/api/v1/movimientos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 7f3a2b10-2b6b-4b8a-9c3f-1a2b3c4d5e6f" \
  -d '{"categoria_movimiento":"venta","cantidad_movida":1,"id_producto":1}'
```

- **Atómica:** la clave es una columna con índice único en esta base de datos.
  Dos pedidos simultáneos con la misma clave no pueden crear dos movimientos
  (el segundo recibe el movimiento del primero). Antes se consultaba Redis y se
  guardaba después en pasos separados, lo que dejaba una carrera.
- **Reutilizar la clave con otro contenido** (otra categoría, cantidad,
  producto o DNI) devuelve `422`.
- **No depende de Redis** ni caduca: vive mientras exista el movimiento.
- Sin header, cada request crea un movimiento nuevo (como antes).

### Evento publicado (outbox)

Al **confirmar** el movimiento se guarda `movimiento.registrado` en la tabla
`outbox_eventos`, en la misma transacción. El relay
(`app/messaging/outbox.py`, un hilo que arranca con la app) lo publica en el
exchange `stock.events` con confirmación del broker y lo marca como publicado.
Con RabbitMQ caído el evento queda pendiente y se reintenta con backoff (5 s
hasta 60 s); sobrevive a reinicios del servicio. La entrega es *at-least-once*:
el worker es idempotente por `id_movimiento`. Detalles y garantías en
`EVENTOS.md`.

## Validación de entrada y encabezados de seguridad

**Validación.** `dni_cliente` y `dni_empleado` aceptan solo de 1 a 8 dígitos
(`^[0-9]{1,8}$`), el mismo criterio que ya aplica el formulario del panel (o
`null` si no se informa). Cualquier otro valor, incluidos los caracteres de
inyección, devuelve `400` con el formato de error del proyecto y no llega a
`productos` ni a la base. `categoria_movimiento` y `cantidad_movida` ya
rechazaban valores fuera de su tipo.

**Headers.** Un middleware agrega `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY` y `Content-Security-Policy: frame-ancestors 'none'` a
toda respuesta, incluidos los errores. El `500` inesperado lo arma Starlette
por fuera de los middlewares, así que su handler los recibe explícitos.

Ambos puntos están cubiertos por `tests/test_bateria_minima.py`.

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/movimientos.db` | Base propia de Movimientos |
| `PRODUCTOS_SERVICE_URL` | `http://localhost:8000` | URL interna del servicio Productos |
| `PRODUCTOS_SERVICE_TIMEOUT` | `5` (segundos) | Timeout de las llamadas HTTP a Productos |
| `AUTH_SECRET_KEY` | — (obligatoria) | Mismo secreto que `auth` y `productos`, para validar JWT |
| `REDIS_URL` | `redis://localhost:6379/0` | Blacklist de tokens revocados. `memory://` en modo local |
| `AUTH_SERVICE_URL` | — | **Solo modo local** (`REDIS_URL=memory://`): URL de `auth`, a quien se consulta si un token fue revocado |
| `RABBITMQ_URL` | `amqp://admin:admin123@localhost:5672/` | Para publicar `movimiento.registrado` |
| `RABBITMQ_EXIGIR_RUTEO` | `true` | Con `true`, un evento sin ninguna cola destino queda pendiente en el outbox en vez de descartarse |
| `OUTBOX_RELAY_HABILITADO` | `true` | Si es `false` no arranca el relay (los tests y el modo local lo desactivan) |
| `OUTBOX_INTERVALO_SEGUNDOS` | `5` | Cada cuánto el relay reintenta publicar pendientes (con backoff hasta 60 s ante fallas) |

## Persistencia

SQLite mediante SQLAlchemy, en su propio volumen Docker
(`movimientos_data`). Guarda los movimientos (con su `estado`, `operacion_id` e
`idempotency_key`) y la tabla `outbox_eventos`. Es una base **independiente** de la de `productos`:
cada servicio es dueño de sus datos, y la comunicación entre ellos es
siempre por API REST o eventos, nunca compartiendo tablas.

## Tests

```bash
cd services/movimientos
pip install -r requirements.txt
pytest
```

(En Windows, `.\ejecutar_local.ps1 -Tests` desde la raíz del repositorio hace
todo esto sola; `-Bateria` corre solo `test_bateria_minima.py`.)

Los tests no requieren Productos, Redis ni RabbitMQ corriendo: Productos se
reemplaza por un doble (`ProductosFalso`, en `tests/conftest.py`) que reproduce
su idempotencia por clave, y RabbitMQ por un publicador falso.

Cubren:
- **Delta por categoría** (`test_movimientos.py`): el signo del ajuste
  depende de la categoría (venta/estropeo descuentan, ingreso/devolución
  incrementan).
- **Propagación de errores**: si Productos devuelve 409 (sin stock), no
  queda un movimiento huérfano.
- **Saga y consistencia** (`test_saga_consistencia.py`): camino feliz, rechazos
  definitivos, Productos caído, respuesta perdida con el stock ya cambiado,
  falla del guardado final, 401 sin descarte, evento y confirmación en la misma
  transacción, **idempotencia atómica** (incluye dos pedidos simultáneos con la
  misma clave y la confirmación doble determinista) y reconciliación.
- **Outbox y relay** (`test_outbox.py`): orden, no duplicar, RabbitMQ caído sin
  perder el evento, fallo a mitad de lote, errores anotados en la fila,
  recuperación automática del relay.
- **Publisher** (`test_publisher.py`): confirmación del broker, `mandatory`,
  mensajes persistentes, formato del payload.
- **Migración de esquema** (`test_migraciones.py`): una base con el esquema
  anterior sigue funcionando y conserva sus movimientos.
- **API HTTP** (`test_api_movimientos.py`): códigos de estado, mensajes,
  `Idempotency-Key`, estado `pendiente`, conteo por producto y reconciliación
  (solo admin).
- **Batería de carga y blindaje** (`test_bateria_minima.py`, 13 tests, con las
  fixtures de `conftest.py`): la **carga concurrente** (20 `POST /movimientos`
  simultáneos: se registran 20 movimientos distintos, se piden 20 ajustes a
  Productos, e imprime latencia media y tasa de éxito); la **sanitización** (9
  payloads maliciosos sobre los DNI, la categoría y la cantidad devuelven 400,
  no llegan a Productos ni se guardan; un control confirma que un DNI válido
  y un `null` se aceptan); y los **headers de seguridad** en respuestas 200,
  201, 400, 404 y 500.
