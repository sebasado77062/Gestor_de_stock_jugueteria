# services/productos

API REST del CRUD de la entidad **Producto** del sistema de gestión de stock
para la juguetería, más el endpoint atómico de ajuste de stock que consume
`services/movimientos`. Construida con FastAPI + SQLAlchemy.

## Ejecutar

Ver el README raíz del repositorio para levantar todo con Docker Compose.
Para correr solo este servicio en desarrollo:

```bash
cd services/productos
python -m venv venv && . venv/bin/activate
pip install -r requirements.txt
mkdir -p data   # SQLite no crea directorios intermedios por su cuenta
export AUTH_SECRET_KEY=clave-de-desarrollo
uvicorn app.main:app --reload
```

La variable `AUTH_SECRET_KEY` es obligatoria: el servicio la usa para
validar los JWT emitidos por `services/auth`. Debe ser **la misma** que usa
`auth` para firmar. En Docker llega por `.env`; para correr localmente hay
que exportarla (cualquier string sirve mientras sea consistente con la que
usa el `auth` local).

Documentación interactiva: `http://localhost:8000/docs`.

## Autenticación

Todos los endpoints de la API (excepto `/health`, `/` y el router de seed
si está habilitado) exigen un JWT válido en el header `Authorization:
Bearer <access_token>`.

El servicio **valida el JWT localmente** usando el mismo `AUTH_SECRET_KEY`
que usó `auth` para firmarlo. No llama a `auth` por cada request — eso
agregaría una llamada de red y un punto de falla extra. La única
dependencia de red real es Redis, para consultar la blacklist de tokens
revocados (un logout debe invalidar el token en todos los servicios que lo
acepten, no solo en `auth`).

Roles:

| Rol | Acceso |
|---|---|
| `admin` | CRUD completo, incluido `DELETE` |
| `empleado` | Listar, ver, crear y actualizar. **No** puede eliminar. |

Ver `app/core/jwt_auth.py` para el mecanismo de validación.

## Endpoints

| Método | Ruta | Descripción | Rol mínimo |
|---|---|---|---|
| GET | `/api/v1/productos` | Lista productos. Por defecto solo `activo=true`. `?incluir_inactivos=true` para ver también los dados de baja. | `empleado` |
| GET | `/api/v1/productos/{id}` | Obtiene un producto por ID (activo o no). | `empleado` |
| POST | `/api/v1/productos` | Crea un producto. Acepta `stock_actual` (default 0). | `empleado` |
| PUT | `/api/v1/productos/{id}` | Actualiza datos descriptivos. **No acepta `stock_actual`** (ver más abajo). | `empleado` |
| DELETE | `/api/v1/productos/{id}` | Elimina o da de baja según tenga movimientos asociados (ver más abajo). | `admin` |
| PATCH | `/api/v1/productos/{id}/ajustar-stock` | Ajusta el stock atómicamente. **Endpoint interno**, consumido por `services/movimientos`. | `empleado` |
| GET | `/health` | Healthcheck (sin auth). | — |
| POST | `/api/v1/dev/seed-productos?cantidad=N` | Genera N productos de prueba. Solo desarrollo. | `empleado` |

El contrato completo está exportado en [`openapi.json`](./openapi.json)
(regenerarlo con `python export_openapi.py` tras cualquier cambio de
schemas o rutas) y también disponible en vivo en `/docs` y `/openapi.json`
mientras el servicio corre.

### `PATCH /api/v1/productos/{id}/ajustar-stock`

Este endpoint es el que usa `services/movimientos` para reflejar ventas,
ingresos, estropeos y devoluciones sobre el stock. El body es:

```json
{ "delta": -1 }
```

`delta > 0` incrementa el stock (ingreso, devolución); `delta < 0` intenta
decrementarlo (venta, estropeo). El ajuste se aplica de forma **atómica**
(ver sección Concurrencia más abajo), así que es seguro ante llamadas
concurrentes: nunca deja `stock_actual` negativo.

El token del usuario que originó el movimiento se propaga desde
`movimientos` hacia acá — no se usan credenciales de servicio. Así, las
reglas de rol se aplican siempre sobre el usuario real.

## Decisiones de diseño (issue #6)

### 1. El PUT ya no puede pisar `stock_actual`

**Problema original:** `ProductoUpdate` heredaba todos los campos de
`ProductoBase`, incluido `stock_actual` como obligatorio. Esto forzaba a
cualquier cliente que quisiera editar solo, por ejemplo, el precio, a
mandar también el stock — y si ese valor había cambiado entre que el
cliente cargó el formulario y lo envió (por una venta, una carga de
mercadería, etc.), el PUT lo pisaba con un dato desactualizado, perdiendo
información real de inventario.

**Solución:** `ProductoUpdate` ya no incluye `stock_actual` en absoluto.
El schema deliberadamente no lo declara, así que:
- El contrato OpenAPI documenta explícitamente que el PUT no lo acepta.
- Si un cliente igual lo manda en el body, Pydantic lo ignora (no forma
  parte del modelo, así que nunca llega a `model_dump()`).
- `producto_service.actualizar_producto` solo puede tocar los campos que
  el schema declara: `nombre`, `marca`, `precio_venta`, `stock_minimo`,
  `categoria`.

El stock operativo (`stock_actual`) queda reservado para modificarse
exclusivamente a través de **movimientos de inventario** (entradas,
salidas, ajustes) — nunca editando la ficha del producto. `ProductoCreate`
sí acepta `stock_actual` (con default `0`) porque dar de alta un producto
requiere declarar su stock inicial.

### 2. Baja lógica en vez de borrado físico cuando hay movimientos

**Problema:** borrar físicamente un producto que tiene movimientos de
stock asociados destruiría la trazabilidad histórica de esos movimientos
(¿a qué producto correspondía esa entrada de 50 unidades del mes pasado?).

**Solución:** se agregó el campo `activo: bool` (default `True`) al
modelo `Producto`. El comportamiento de `DELETE /api/v1/productos/{id}`
depende de si el producto tiene movimientos asociados:

- **Sin movimientos:** se borra físicamente, como antes → `204 No Content`.
- **Con movimientos:** se marca `activo = False` (baja lógica) en lugar de
  borrarse → `200 OK` con el producto actualizado en el body, para que el
  cliente distinga ambos casos.

Un producto dado de baja:
- Sigue siendo consultable por `GET /api/v1/productos/{id}` (para ver su
  detalle o su historial).
- **No** aparece en `GET /api/v1/productos` por defecto.
- Sí aparece con `GET /api/v1/productos?incluir_inactivos=true`.

**Limitación actual:** la función
`producto_service.tiene_movimientos_asociados` sigue siendo un *stub* que
siempre devuelve `False` — aunque `services/movimientos` ya existe, la
consulta real a su API todavía no está implementada. Hoy, en la práctica,
todo `DELETE` resulta en borrado físico. El punto de integración está
aislado en esa única función: cuando se agregue la consulta real (vía la
API REST de `movimientos`), no hará falta tocar el router ni el resto del
servicio.

## Concurrencia en el ajuste de stock

### Problema

El endpoint `PATCH /ajustar-stock` puede recibir múltiples requests
concurrentes sobre el mismo producto (por ejemplo, dos ventas simultáneas
desde dos cajas, disparadas ambas por `services/movimientos`). Un ajuste
implementado como "leer stock → validar en Python → escribir nuevo valor"
produce una **condición de carrera** conocida como *lost update*: ambos
requests leen el mismo stock antes de que ninguno escriba, ambos pasan la
validación "hay stock suficiente", y ambos descuentan.

### Resultado incorrecto

Con `stock_actual = 1` y dos ventas simultáneas de 1 unidad cada una:

- **2 ventas aceptadas** sobre un producto que solo tenía 1 unidad
  (sobreventa).
- El stock final no refleja las dos operaciones aceptadas (lost update).

### Mecanismo

La función `producto_service.ajustar_stock_atomico` evalúa la condición de
stock **dentro de la propia sentencia SQL**:

```sql
UPDATE productos
   SET stock_actual = stock_actual + :delta
 WHERE id_producto = :id
   AND stock_actual >= :delta_absoluto   -- solo cuando delta < 0
```

La base de datos resuelve la carrera atómicamente: si dos `UPDATE` intentan
matchear la misma fila, solo uno puede tener éxito. El otro devuelve
`rowcount == 0`, que el router traduce a **409 Conflict**. El resultado es
que nunca se acepta una venta sin stock suficiente, sin importar el orden
de llegada ni el paralelismo.

### Verificación

`tests/test_concurrencia_stock.py` corre dos escenarios con hilos reales
(`threading.Thread` + `threading.Barrier` para forzar el entrelazado):

- `test_ajuste_no_atomico_permite_sobreventa`: reproduce el problema
  (asserta que las 2 ventas se aceptan con un ajuste no atómico).
- `test_ajuste_atomico_previene_sobreventa`: prueba la solución (asserta
  que exactamente una venta se acepta, la otra se rechaza con
  `"sin_stock"`, y el stock nunca queda negativo).

```bash
docker compose exec productos pytest tests/test_concurrencia_stock.py -v
```

## Datos de prueba (`/api/v1/dev/seed-productos`)

Endpoint de utilidad para poblar rápido el inventario con productos
variados (nombre, marca, categoría, precio y stock generados
aleatoriamente dentro de rangos razonables) mientras se prueba la app —
por ejemplo, para ver el listado con muchos registros o el indicador de
"stock bajo" con datos reales. Se dispara desde el botón **"🎲 Cargar
datos de prueba"** en el panel del frontend, junto al título del
inventario.

- `POST /api/v1/dev/seed-productos?cantidad=N` (1 a 200, default 20).
- Agrega productos al inventario existente, no lo reemplaza ni lo limpia.
- No forma parte del CRUD de negocio de Producto (vive en un router
  aparte, `app/routers/seed_router.py`), y se puede deshabilitar con la
  variable de entorno `HABILITAR_SEED_ENDPOINT=false` (ver
  `.env.example`) para no exponerlo en un entorno que no sea de
  desarrollo/pruebas.
- Requiere estar autenticado (rol `empleado` o `admin`).

## Persistencia

SQLite mediante SQLAlchemy, con la URL de conexión configurable por la
variable de entorno `DATABASE_URL` (ver `.env.example` en la raíz del
repo). En Docker, el archivo vive en el volumen nombrado `productos_data`,
montado en `/app/data` — nunca en el repositorio (ver *Limitaciones*).

**Por qué SQLite y no Postgres/MySQL:** es un proyecto académico de alcance
acotado, con un único servicio consumidor de esta base (además de
`movimientos`, que accede por API, no por SQL). SQLite no requiere levantar
un contenedor adicional ni gestionar credenciales, lo que simplifica la
reproducibilidad (`docker compose up` sin dependencias externas). La capa
de acceso a datos está detrás de SQLAlchemy, así que migrar a Postgres en
el futuro implica cambiar `DATABASE_URL` y agregar el driver
correspondiente al `requirements.txt`, sin tocar modelos, schemas ni
lógica de negocio.

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/jugueteria.db` | URL de la base propia |
| `AUTH_SECRET_KEY` | — (obligatoria) | Mismo secreto que `auth`, para validar JWT localmente |
| `REDIS_URL` | `redis://localhost:6379/0` | Para consultar la blacklist de tokens revocados. Se puede usar `memory://` en desarrollo (usa `fakeredis`) |
| `HABILITAR_SEED_ENDPOINT` | `true` | Habilita/deshabilita el router de seed |

## Limitaciones conocidas

- **`tiene_movimientos_asociados` es un stub** (ver arriba): aunque
  `services/movimientos` ya existe, la consulta real todavía no está
  implementada, así que la baja lógica no se activa en la práctica. El
  mecanismo sí está probado (ver `tests/test_baja_logica_productos.py`,
  que lo ejercita simulando la respuesta con `monkeypatch`).
- **Sin paginación** en `GET /api/v1/productos`: devuelve el listado
  completo. Con el volumen de datos esperado para una juguetería (decenas
  o cientos de productos) no es un problema práctico, pero no escalaría
  a catálogos de miles de ítems sin agregar `limit`/`offset`.
- **SQLite en un único archivo**: no soporta escrituras concurrentes de
  alto volumen (es una limitación del motor, no de la lógica). Para el
  alcance actual es suficiente; la concurrencia crítica está resuelta a
  nivel de sentencia SQL (ver sección Concurrencia). Ver la nota de
  persistencia sobre migración a Postgres.
- **CORS abierto (`allow_origins=["*"]`)**: correcto para desarrollo y para
  el alcance de este proyecto, pero en un despliegue productivo debería
  restringirse al dominio real del frontend.
- **Fail-open si Redis está caído**: si Redis no responde, la validación
  de la blacklist de tokens falla hacia "no revocado" (se sigue validando
  firma y expiración). Es una decisión para no bloquear todo el servicio
  por una dependencia secundaria; la contrapartida es que un logout no
  invalida el token hasta que expire solo.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

La suite cubre:
- **CRUD** (`tests/test_crud_productos.py`): camino feliz de las 5
  operaciones y sus 404.
- **Validaciones** (`tests/test_validaciones_productos.py`): campos
  obligatorios, valores negativos, longitudes, tipos inválidos, y en
  particular que el PUT nunca modifique `stock_actual` aunque el cliente
  intente enviarlo.
- **Baja lógica** (`tests/test_baja_logica_productos.py`): comportamiento
  de `DELETE` con y sin movimientos simulados, filtro `incluir_inactivos`.
- **Concurrencia** (`tests/test_concurrencia_stock.py`): reproduce la
  sobreventa con un ajuste no atómico y verifica que el ajuste atómico la
  previene.
- **Autenticación** (`tests/test_autenticacion.py`): 401 sin token, 401
  con token inválido, 403 con rol insuficiente, y verificación de que
  cada endpoint respeta el rol mínimo.
- **Datos de prueba** (`tests/test_seed_productos.py`): generación de
  lotes, límites de cantidad (1-200), y el flag `HABILITAR_SEED_ENDPOINT`.
- **Integración** (`tests/test_integracion.py`): flujos completos
  encadenados (crear → editar → listar → eliminar) y healthcheck.

Cada test corre contra una base SQLite en memoria aislada (no comparte
estado entre tests, no toca el filesystem ni la base real del contenedor).
La autenticación se simula con overrides de dependencias (ver
`tests/conftest.py`), salvo los tests específicos de auth, que usan el
mecanismo real con JWT firmados.