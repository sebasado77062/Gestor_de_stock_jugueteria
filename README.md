# Gestor de Stock — Juguetería

Sistema de gestión de stock para una juguetería. Compuesto por tres APIs
REST (FastAPI), un worker de mensajería asíncrona y un panel de
administración (HTML/JS) servido con nginx.

## Estructura del repositorio

```
.
├── docker-compose.yml
├── .env.example
├── ejecutar_local.ps1     # Windows, sin Docker: levanta auth, productos, movimientos y frontend
├── docs/
│   └── ARQUITECTURA.md    # Diagramas (servicios, capas, flujos, concurrencia)
├── services/
│   ├── auth/              # Login, JWT (access/refresh), roles (FastAPI + SQLite + Redis)
│   ├── productos/         # CRUD de Producto + ajuste atómico de stock (FastAPI + SQLite)
│   ├── movimientos/       # Registro histórico de movimientos + publicación de eventos (FastAPI + SQLite + Redis + RabbitMQ)
│   └── worker-alertas/    # Consumidor RabbitMQ: alertas de stock bajo (con DLQ e idempotencia)
└── frontend/              # Panel de administración, servido con nginx
```

Cada carpeta bajo `services/` es un servicio independiente, con su propio
Dockerfile, dependencias y (cuando corresponde) su propia base de datos.
Ver [`docs/ARQUITECTURA.md`](./docs/ARQUITECTURA.md) para diagramas de cómo
se relacionan.

## Requisitos

- Docker y Docker Compose (plugin `compose`: `docker compose`, no el binario
  viejo `docker-compose`).

No se necesita Python ni Node.js instalados localmente: todo corre dentro de
contenedores.

Alternativa opcional en Windows, sin Docker: Python 3.10 a 3.13 (recomendado
3.12) y el script `ejecutar_local.ps1`. Ver "Desarrollo local sin Docker".

## Puesta en marcha desde cero

```bash
cp .env.example .env
docker compose up --build
```

Esto levanta, en orden (según `depends_on` + healthchecks):

1. **`redis`** — blacklist de tokens JWT + idempotencia de movimientos.
2. **`rabbitmq`** — broker de mensajería. Panel de administración en
   `http://localhost:15672` (usuario/clave en `.env`, por defecto
   `admin` / `admin123`).
3. **`auth`** en `http://localhost:8001` — crea los usuarios de prueba y
   firma los JWT.
4. **`productos`** en `http://localhost:8000` — valida JWT localmente y
   expone el CRUD + ajuste atómico de stock.
5. **`movimientos`** en `http://localhost:8002` — registra movimientos,
   ajusta stock en `productos` y publica eventos en RabbitMQ.
6. **`worker-alertas`** — consumidor asíncrono. No expone puerto HTTP, se
   verifica por logs (`docker compose logs -f worker-alertas`).
7. **`frontend`** en `http://localhost:8080` — con su `config.js` generado
   en runtime a partir de las variables de entorno.

Abrir `http://localhost:8080` en el navegador para usar el panel.

Documentación interactiva (Swagger) de cada API:

- Productos: `http://localhost:8000/docs`
- Movimientos: `http://localhost:8002/docs`
- Auth: `http://localhost:8001/docs`

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

| Rol        | Email               | Contraseña     | Permisos                                                       |
|------------|---------------------|----------------|----------------------------------------------------------------|
| `admin`    | `admin@test.com`    | `admin12345`   | Todo: productos (incluido eliminar) y gestión de usuarios.      |
| `empleado` | `empleado@test.com` | `empleado123`  | Ver/crear/editar productos y registrar movimientos. No puede eliminar. |

Los valores salen de `PRUEBA_ADMIN_*` y `PRUEBA_EMPLEADO_*` en `.env`. Para
un entorno real, poner `HABILITAR_USUARIOS_PRUEBA=false`: no se crean los
usuarios y el frontend deja de recibir las credenciales. Opcionalmente se
puede definir un admin propio con `AUTH_ADMIN_EMAIL` / `AUTH_ADMIN_PASSWORD`
(se crea solo si la base de usuarios está vacía).

> Si cambiás las contraseñas de prueba en `.env` con una base ya creada, el
> usuario existente conserva la anterior: `docker compose down -v` para
> empezar de cero.

## Cómo probar el sistema de punta a punta

Guía rápida para verificar manualmente que el sistema completo funciona:
autenticación, CRUD de productos, registro de movimientos, ajuste atómico de
stock y alerta asíncrona de stock bajo.

### Preparación

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

Esperar a que todos los contenedores queden `healthy` (excepto
`worker-alertas`, que se verifica por logs). Abrir:

- **Frontend:** http://localhost:8080
- **Panel de RabbitMQ:** http://localhost:15672 (usuario/clave en `.env`, por defecto `admin` / `admin123`)

### Paso 1 — Login

1. Abrir `http://localhost:8080`.
2. Usar el botón de **acceso rápido como Admin** (o loguearse con
   `admin@test.com` / `admin12345`).
3. Verificar que aparece el nombre y rol del usuario en el header.

### Paso 2 — Crear un producto con stock bajo

1. Ir a la pestaña **Inventario**.
2. Crear un producto con:
   - Nombre: `Producto de prueba`
   - Precio: `500`
   - Stock actual: `5`
   - Stock mínimo: `10` ← **más alto que el actual, para forzar la alerta**
3. Guardar. El producto aparece en la tabla con la etiqueta de stock bajo.

> Cada campo tiene un texto de ayuda y un tooltip ("?"), y la guía
> desplegable de la pantalla incluye un botón que completa el formulario con
> un ejemplo.

### Paso 3 — Registrar una venta

1. Ir a la pestaña **Movimientos**.
2. Producto: elegir "Producto de prueba" del dropdown (escribir "Produ" para
   filtrar).
3. Categoría: **Venta (descuenta)**.
4. Cantidad: `2`.
5. Click en **Registrar movimiento**.

Verificar:
- Aparece un mensaje verde "Movimiento registrado correctamente".
- El movimiento aparece en la tabla de abajo con la categoría marcada en rojo.
- Volver a la pestaña **Inventario**: el stock bajó de `5` a `3`.

### Paso 4 — Verificar la alerta asíncrona

El evento `movimiento.registrado` se publicó en RabbitMQ. El worker lo
consumió y evaluó que el stock quedó por debajo del mínimo. Verificar en los
logs:

```bash
docker compose logs worker-alertas | Select-Object -Last 5
```

Debería aparecer algo como:

```
[ALERTA STOCK BAJO] producto=Producto de prueba (id=1) stock_actual=3 stock_minimo=10 -- disparado por movimiento id_movimiento=1
```

También se puede ver el flujo en el panel de RabbitMQ:
- **Exchanges** → `stock.events` con publish count > 0.
- **Queues** → `alertas.stock.bajo` con acknowledges > 0.

### Paso 5 — Probar la concurrencia (opcional, vía tests)

La concurrencia se verifica con los tests, que simulan ventas simultáneas
reales con hilos:

```bash
docker compose exec productos pytest tests/test_concurrencia_stock.py -v
```

Esperado: los 2 tests pasan. Uno demuestra el problema (sobreventa con un
ajuste no atómico), el otro demuestra que el ajuste atómico la previene.

### Paso 6 — Probar la idempotencia (opcional)

Con el token obtenido del navegador (DevTools → Application → Local Storage
→ `jugueteria.sesion` → campo `access`), enviar dos veces el mismo request
con la misma `Idempotency-Key`:

```powershell
$TOKEN = "pegar-token-aca"

curl.exe -X POST http://localhost:8002/api/v1/movimientos `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer $TOKEN" `
  -H "Idempotency-Key: prueba-manual-001" `
  -d '{\"categoria_movimiento\":\"venta\",\"cantidad_movida\":1,\"id_producto\":1}'
```

Ejecutar el mismo comando dos veces. Ambas respuestas deben traer el **mismo
`id_movimiento`**, y el stock del producto debe haber bajado **una sola
unidad** (no dos).

### Paso 7 — Probar errores y envíos duplicados (opcional, manual)

Verificaciones del panel (ver la sección "Interfaz del panel"):

1. **Error por campo:** en Inventario, escribir solo espacios en *Nombre* y
   guardar. El servidor responde 400 y el mensaje aparece **debajo del campo**.
2. **Doble clic:** en las herramientas del navegador (F12 → Red) elegir
   "Slow 3G" y hacer doble clic en *Guardar producto*. Se ve el spinner y se
   crea **un solo** producto.
3. **Sin conexión:** elegir "Offline" y guardar. Aparece un aviso de
   conexión y los datos siguen en el formulario.
4. **Stock insuficiente:** en Movimientos, registrar una venta con más
   unidades que el stock. El aviso bajo *Cantidad* indica cuántas hay
   disponibles.

### Resumen de lo que se prueba

| Paso | Qué se valida |
|---|---|
| 1 | Autenticación JWT + roles |
| 2 | CRUD de productos + validación de stock mínimo |
| 3 | Vertical completa: frontend → movimientos → productos → DB |
| 4 | Mensajería asíncrona: publish en RabbitMQ + consumo por el worker |
| 5 | Concurrencia: ajuste atómico bajo ventas simultáneas |
| 6 | Idempotencia: reintento con la misma clave no duplica |
| 7 | Errores por campo, anti-duplicados y manejo de fallas de red en el panel |

## Concurrencia en el ajuste de stock

### Problema

`POST /api/v1/movimientos` puede recibir múltiples requests concurrentes
sobre el mismo producto (por ejemplo, dos ventas simultáneas desde dos
cajas). Un ajuste implementado como "leer stock → validar en Python →
escribir nuevo valor" produce una **condición de carrera conocida como
_lost update_ o _read-modify-write_**: ambos requests leen el mismo stock
antes de que ninguno escriba, ambos pasan la validación "hay stock
suficiente", y ambos descuentan.

### Resultado incorrecto

Con `stock_actual = 1` y dos ventas simultáneas de 1 unidad cada una, un
ajuste no atómico produce:

- **2 ventas aceptadas** sobre un producto que solo tenía 1 unidad
  (sobreventa).
- El stock final no refleja las dos operaciones aceptadas: la segunda
  escritura pisa a la primera con el mismo valor calculado de forma
  independiente (el stock queda en 0, pero se "vendieron" 2 unidades).

El resultado es un sistema que reporta más ventas de las que el stock
permitía, sin que ninguna operación haya fallado.

### Mecanismo

El endpoint `PATCH /api/v1/productos/{id}/ajustar-stock` (en
`services/productos/app/services/producto_service.py`, función
`ajustar_stock_atomico`) evalúa la condición de stock **dentro de la propia
sentencia SQL**:

```sql
UPDATE productos
   SET stock_actual = stock_actual + :delta
 WHERE id_producto = :id
   AND stock_actual >= :delta_absoluto   -- solo cuando delta < 0
```

La base de datos resuelve la carrera atómicamente: si dos UPDATE intentan
matchear la misma fila, solo uno puede tener éxito. El otro devuelve
`rowcount == 0`, que se traduce a un **409 Conflict** en el endpoint. El
resultado es que nunca se acepta una venta sin stock suficiente, sin
importar el orden de llegada ni el paralelismo.

### Verificación

`services/productos/tests/test_concurrencia_stock.py` corre dos escenarios
con hilos reales (`threading.Thread` + `threading.Barrier` para forzar el
entrelazado):

- `test_ajuste_no_atomico_permite_sobreventa`: reproduce el problema
  (asserta que las 2 ventas se aceptan).
- `test_ajuste_atomico_previene_sobreventa`: prueba la solución (asserta
  que exactamente una venta se acepta, la otra se rechaza con `"sin_stock"`,
  y el stock queda consistente).

Se corren con:

```bash
docker compose exec productos pytest tests/test_concurrencia_stock.py -v
```

## Idempotencia y mensajería asíncrona

### Idempotencia del POST de movimientos

`POST /api/v1/movimientos` acepta un header opcional `Idempotency-Key`. Si
el cliente reintenta la misma solicitud (por un timeout, un doble clic, un
problema de red), el servicio:

1. Consulta Redis (`idempotencia:movimientos:<key>`).
2. Si la clave existe, devuelve el mismo movimiento ya registrado, **sin
   volver a ajustar stock**.
3. Si no existe, procesa normalmente y guarda la clave con un TTL de 24 h.

Si Redis está caído, el servicio **degrada de forma controlada**: sigue
funcionando pero pierde la garantía de idempotencia en ese request puntual
(se loguea la excepción y se continúa).

El **frontend** genera la clave (un UUID por intento de registro). Si el
intento termina sin respuesta definitiva (timeout, corte de red o 5xx) y los
datos no cambiaron, el reintento reutiliza la misma clave; con una respuesta
definitiva o con datos distintos se genera una nueva. La garantía tiene
límites: la clave se consulta y se guarda en pasos separados (no es atómico)
y solo se registra si el primer intento llegó a completarse. `POST
/api/v1/productos` no tiene idempotencia.

### Evento `movimiento.registrado`

Al persistir un movimiento, `movimientos` publica el evento
`movimiento.registrado` en el exchange `stock.events` (tipo `topic`,
durable). El evento es **autocontenido**: incluye un snapshot del producto
(`stock_actual`, `stock_minimo`, `nombre`) al momento del ajuste, para que
los consumidores no tengan que llamar a `productos` (que exige JWT de
usuario, y un worker asíncrono no tiene uno).

Una falla al publicar (RabbitMQ caído) **no impide** que el endpoint
devuelva `201 Created`: el movimiento ya quedó persistido y el ajuste de
stock ya se aplicó. Solo se pierde, en ese caso puntual, la notificación
asíncrona. Se loguea para su seguimiento.

Ver `services/movimientos/EVENTOS.md` para el catálogo completo del evento
(exchange, routing key, payload, garantías de entrega).

### Worker de alertas

`services/worker-alertas` consume el exchange `stock.events` y evalúa si el
stock resultante de un producto quedó por debajo del mínimo. Es
**idempotente por `id_movimiento`** (verifica una base SQLite local antes
de reprocesar) porque RabbitMQ garantiza entrega *at-least-once*: el mismo
mensaje puede llegar más de una vez.

Tiene **reintentos con backoff** y **Dead-Letter Queue**:

- Si el procesamiento falla, el mensaje se rechaza (`nack`) y RabbitMQ lo
  dead-letterea a `alertas.stock.bajo.retry` (TTL 5 s), que lo devuelve a
  la cola principal.
- Después de `MAX_REINTENTOS` (5 por defecto, contados a partir del header
  `x-death`), el mensaje se mueve a `alertas.stock.bajo.dlq` para
  inspección manual desde el panel de RabbitMQ.

Ver `services/worker-alertas/README.md` para el detalle de la topología de
colas.

### Verificación de la mensajería

```bash
# Ver que el worker arrancó y está consumiendo
docker compose logs -f worker-alertas

# Disparar una alerta: crear un producto con stock_minimo > stock_actual,
# registrar una venta desde el frontend, y mirar el log del worker:
# [ALERTA STOCK BAJO] producto=... stock_actual=2 stock_minimo=7 ...

# Panel de RabbitMQ
open http://localhost:15672   # usuario/clave en .env
```

## Interfaz del panel (frontend)

El panel (`frontend/index.html`, `app.js` y `movimientos.js`) incluye, en
Inventario y en Movimientos:

- **Errores de validación junto al campo:** los errores 400 del backend
  (`detalles`) se traducen a español y se muestran bajo el campo
  correspondiente, con borde rojo y `aria-invalid`. En Movimientos también se
  muestran junto al campo el producto no elegido, el stock insuficiente (con
  las unidades disponibles) y el producto inexistente.
- **Ayuda al usuario:** texto de ayuda con regla y ejemplo en cada campo,
  tooltip ("?") con la explicación de qué se ingresa, y una guía desplegable
  por pantalla con un botón que completa el formulario con un ejemplo.
- **Envíos seguros:** el botón se bloquea con un spinner mientras se procesa
  la petición y se ignoran envíos repetidos (doble clic, Enter). Las
  peticiones tienen un tiempo máximo de espera de 15 s y, ante fallas de red,
  los datos quedan en el formulario con un aviso claro. Al crear un producto,
  si no hay respuesta, el aviso pide revisar el inventario antes de
  reintentar.
- **Accesibilidad básica:** `lang="es"`, labels asociados, tooltips operables
  con teclado (Escape los oculta) y mensajes anunciados con `aria-live`.

Ver el flujo completo en
[`docs/ARQUITECTURA.md`](./docs/ARQUITECTURA.md#frontend-validación-errores-y-envíos).

## Variables de entorno

Ver `.env.example` para el detalle de cada variable. `AUTH_SECRET_KEY`
(firma de los JWT) es **obligatoria** y hoy `.env.example` no la incluye:
agregar a mano una línea `AUTH_SECRET_KEY=<un valor largo y aleatorio>` en el
`.env`; sin ella `docker compose up` se detiene con un mensaje pidiéndola. El
archivo `.env` real **no se sube al repositorio** (ver `.gitignore`).

## Persistencia de datos

Cada servicio con estado tiene su propio volumen Docker, montado en
`/app/data` dentro del contenedor y **excluido del repositorio** (ver
`.gitignore`):

| Servicio | Volumen | Contenido |
|---|---|---|
| `auth` | `auth_data` | `auth.db` (usuarios, tokens de refresh) |
| `productos` | `productos_data` | `jugueteria.db` (catálogo + stock) |
| `movimientos` | `movimientos_data` | `movimientos.db` (histórico) |
| `worker-alertas` | `alertas_data` | `alertas_procesadas.db` (idempotencia local) |
| `redis` | `redis_data` | Claves de idempotencia y blacklist de JWT |
| `rabbitmq` | `rabbitmq_data` | Colas y mensajes persistentes |

Para precargar datos de ejemplo en `productos` una vez que el servicio está
corriendo:

```bash
docker compose exec productos python seed_data.py
```

## Desarrollo local sin Docker (opcional)

**Windows, sin instalar Redis ni RabbitMQ.** Requiere Python 3.10 a 3.13
(recomendado 3.12; las dependencias fijadas no se instalan en 3.14 o
superior). Desde PowerShell, en la raíz del proyecto ya descomprimida:

```powershell
powershell -ExecutionPolicy Bypass -File .\ejecutar_local.ps1
```

El script crea un entorno virtual por servicio, instala las dependencias
(`requirements-local.txt`) y abre una ventana de PowerShell para cada uno:

| Componente | Puerto |
|---|---|
| `auth` | 8001 |
| `productos` | 8000 |
| `movimientos` | 8002 |
| `frontend` (`python -m http.server`) | 8080 |

Los cuatro puertos deben estar libres. Cada servicio usa un Redis en memoria
(`REDIS_URL=memory://`, vía `fakeredis`). Es solo para probar; limitaciones
de este modo:

- **No corren RabbitMQ ni `worker-alertas`.** Los movimientos se registran
  igual, pero la publicación del evento falla (se ve un error de AMQP en la
  ventana de `movimientos`, sin afectar el `201`) y no se procesan alertas de
  stock bajo.
- **Logout parcial:** cada servicio tiene su propio Redis en memoria. `auth`
  revoca los tokens en el suyo, pero `productos` y `movimientos` siguen
  aceptando el access token hasta que expira (15 min). Con `docker compose`
  (Redis compartido) queda revocado en los tres.
- El estado en memoria (bloqueo por intentos de login, rotación de refresh
  tokens, idempotencia de movimientos) se pierde al reiniciar el servicio.

Si el script falla:

- Ejecutarlo con **PowerShell**, no con Python (un `SyntaxError` sobre `@'`
  indica que se corrió con `python`).
- Si aparece `No module named pip`, `No pyvenv.cfg file` o
  `No module named uvicorn`, el entorno virtual quedó a medias (suele pasar
  por el antivirus o una versión de Python incompatible): borrar la carpeta
  `venv` de ese servicio y volver a ejecutar.
- Si el navegador no muestra los cambios del frontend, recargar con Ctrl+F5.

Alternativa manual: cada servicio se puede correr con `uvicorn app.main:app
--reload` en su propio virtualenv, ver el README de cada uno bajo
`services/`.

## Contrato de la API

Los contratos OpenAPI de cada servicio están exportados:

- `services/productos/openapi.json`
- `services/movimientos/openapi.json`
- `services/auth/openapi.json`

También están disponibles en vivo mientras los servicios corren:

- Productos: `http://localhost:8000/docs` / `/openapi.json`
- Movimientos: `http://localhost:8002/docs` / `/openapi.json`
- Auth: `http://localhost:8001/docs` / `/openapi.json`

## Tests

Tests por servicio (todos corren contra una base SQLite en memoria o
temporal, aislados entre sí, sin requerir dependencias externas):

```bash
docker compose exec productos pytest
docker compose exec movimientos pytest
docker compose exec worker-alertas pytest
```

(o localmente dentro de cada carpeta, ver el README de cada servicio).

Cobertura actual:

| Servicio | Tests | Qué cubre |
|---|---|---|
| `productos` | 51 | CRUD, validaciones, baja lógica, autenticación, **concurrencia** (`test_concurrencia_stock.py`) |
| `movimientos` | 8 | Delta por categoría, propagación de errores, **idempotencia** (`test_idempotencia.py`) |
| `worker-alertas` | 6 | Idempotencia, evaluación de alerta, conteo de reintentos |

Ver el detalle de qué cubre cada archivo en el README de cada servicio. El
frontend no tiene tests automáticos en el repositorio: se verifica
manualmente (ver Paso 7 de "Cómo probar el sistema de punta a punta").

## Calidad y decisiones de diseño

Correcciones de diseño relevantes (con justificación completa y tests que
las respaldan en el README de cada servicio):

- **El PUT de productos ya no puede pisar `stock_actual`.** El schema de
  actualización no incluye ese campo: el stock se modifica exclusivamente
  vía movimientos de inventario.
- **Baja lógica en vez de borrado físico** cuando un producto tiene
  movimientos asociados (se marca `activo=false`), para preservar la
  trazabilidad histórica. Está implementada, pero hoy no se activa en uso
  real porque `tiene_movimientos_asociados` es un placeholder (ver
  "Limitaciones conocidas").
- **Concurrencia resuelta con `UPDATE ... WHERE`** en lugar de locking
  optimista o pesimista: es la solución más simple que da la atomicidad
  necesaria sin agregar coordinación entre procesos. Ver sección
  "Concurrencia en el ajuste de stock".
- **Evento autocontenido** en lugar de "evento + callback": el worker no
  necesita llamar a `productos` para evaluar la alerta. Esto elimina la
  necesidad de autenticación de servicio a servicio y reduce el
  acoplamiento entre servicios.
- **Idempotencia en Redis con TTL** en lugar de guardar todas las claves
  indefinidamente: el TTL de 24 h acota el crecimiento de memoria y cubre
  el horizonte realista de reintentos de un cliente.

## Healthchecks

- `auth`: `GET /health` → `{"status": "ok", "service": "auth"}`
- `productos`: `GET /health` → `{"status": "ok", "service": "productos"}`
- `movimientos`: `GET /health` → `{"status": "ok", "service": "movimientos"}`
- `frontend`: `GET /healthz` → `200 ok` (nginx sirviendo contenido)
- `redis`: `redis-cli ping` → `PONG`
- `rabbitmq`: `rabbitmq-diagnostics ping`
- `worker-alertas`: no expone HTTP (proceso de background). Se monitorea
  por logs: `docker compose logs -f worker-alertas`.

`docker compose ps` muestra el estado (`healthy` / `unhealthy`) de cada
servicio una vez que Docker ejecuta los healthchecks definidos en
`docker-compose.yml`.

## Limitaciones conocidas

Ver [`docs/ARQUITECTURA.md`](./docs/ARQUITECTURA.md#limitaciones-del-sistema-resumen)
para el detalle de cada una. Las más relevantes:

- `tiene_movimientos_asociados` en `productos` no consulta realmente a
  `movimientos` (es un placeholder).
- SQLite en lugar de Postgres: adecuado para la etapa actual, limita la
  concurrencia real y la separación física de datos.
- `worker-alertas` no tiene healthcheck HTTP.
- Sin pipeline de CI (los tests se corren manualmente).
- El ajuste de stock (en `productos`) y el guardado del movimiento (en
  `movimientos`) no son atómicos: no hay compensación si falla el segundo
  paso.
- Idempotencia parcial: no es atómica en `movimientos` y `productos` no la
  tiene.
- Si RabbitMQ está caído al registrar un movimiento, el evento se pierde
  (no hay outbox).
- La tabla de Inventario marca stock bajo con `stock_actual <=
  stock_minimo`, pero el worker alerta solo con `stock_actual <
  stock_minimo`.
- En el modo local sin Docker, el logout no invalida el token en
  `productos` ni `movimientos` (ver "Desarrollo local sin Docker").