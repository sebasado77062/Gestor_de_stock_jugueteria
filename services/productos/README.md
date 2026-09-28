# services/productos

API REST del CRUD de la entidad **Producto** del sistema de gestión de stock
para la juguetería. Construida con FastAPI + SQLAlchemy.

## Ejecutar

Ver el README raíz del repositorio para levantar todo con Docker Compose.
Para correr solo este servicio en desarrollo:

```bash
cd services/productos
python -m venv venv && . venv/bin/activate
pip install -r requirements.txt
mkdir -p data   # SQLite no crea directorios intermedios por su cuenta
uvicorn app.main:app --reload
```

Documentación interactiva: `http://localhost:8000/docs`.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/productos` | Lista productos. Por defecto solo `activo=true`. `?incluir_inactivos=true` para ver también los dados de baja. |
| GET | `/api/v1/productos/{id}` | Obtiene un producto por ID (activo o no). |
| POST | `/api/v1/productos` | Crea un producto. Acepta `stock_actual` (default 0). |
| PUT | `/api/v1/productos/{id}` | Actualiza datos descriptivos. **No acepta `stock_actual`** (ver más abajo). |
| DELETE | `/api/v1/productos/{id}` | Elimina o da de baja según tenga movimientos asociados (ver más abajo). |
| GET | `/health` | Healthcheck. |
| POST | `/api/v1/dev/seed-productos?cantidad=N` | Genera N productos de prueba con datos aleatorios (1-200, default 20). Solo desarrollo, ver más abajo. |

El contrato completo está exportado en [`openapi.json`](./openapi.json)
(regenerarlo con `python export_openapi.py` tras cualquier cambio de
schemas o rutas) y también disponible en vivo en `/docs` y `/openapi.json`
mientras el servicio corre.

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
salidas, ajustes) una vez que `services/movimientos` exista — nunca
editando la ficha del producto. `ProductoCreate` sí acepta `stock_actual`
(con default `0`) porque dar de alta un producto requiere declarar su
stock inicial.

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

**Limitación actual:** la función `producto_service.tiene_movimientos_asociados`
es un *stub* que siempre devuelve `False`, porque `services/movimientos`
todavía no existe (ver su README). Hoy, en la práctica, todo `DELETE`
resulta en borrado físico. El punto de integración ya está aislado en esa
única función: cuando `movimientos` exista, ahí se agregará la consulta
real (vía su API REST, según el patrón de comunicación decidido para el
proyecto) sin tener que tocar el router ni el resto del servicio.

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
- El script `seed_data.py` (5 productos fijos, pensado para una demo
  puntual) se mantiene sin cambios y sigue siendo válido para ese uso;
  este endpoint es el complemento para generar volumen variable de datos
  desde la propia interfaz, sin tocar la terminal.

## Persistencia

SQLite mediante SQLAlchemy, con la URL de conexión configurable por la
variable de entorno `DATABASE_URL` (ver `.env.example` en la raíz del
repo). En Docker, el archivo vive en el volumen nombrado `productos_data`,
montado en `/app/data` — nunca en el repositorio (ver *Limitaciones*).

**Por qué SQLite y no Postgres/MySQL:** es un proyecto académico de alcance
acotado, con un único servicio consumidor de esta base y sin necesidad de
concurrencia de escritura alta. SQLite no requiere levantar un contenedor
adicional ni gestionar credenciales, lo que simplifica la reproducibilidad
(`docker compose up` sin dependencias externas). La capa de acceso a datos
está detrás de SQLAlchemy, así que migrar a Postgres en el futuro implica
cambiar `DATABASE_URL` y agregar el driver correspondiente al
`requirements.txt`, sin tocar modelos, schemas ni lógica de negocio.

## Limitaciones conocidas

- **Sin autenticación/autorización.** Cualquiera que acceda a la URL del
  servicio puede crear, editar o eliminar productos. Aceptable para el
  alcance académico actual; quedaría pendiente para un entorno real.
- **`tiene_movimientos_asociados` es un stub** (ver arriba): hasta que
  `services/movimientos` exista, el soft delete nunca se activa en la
  práctica, aunque el mecanismo ya está probado (ver `tests/test_baja_logica_productos.py`,
  que lo ejercita simulando la respuesta real con `monkeypatch`).
- **Sin paginación** en `GET /api/v1/productos`: devuelve el listado
  completo. Con el volumen de datos esperado para una juguetería (decenas
  o cientos de productos) no es un problema práctico, pero no escalaría
  a catálogos de miles de ítems sin agregar `limit`/`offset`.
- **SQLite en un único archivo**: no soporta escrituras concurrentes de
  alto volumen. Ver la nota de persistencia sobre migración a Postgres.
- **CORS abierto (`allow_origins=["*"]`)**: correcto para desarrollo y para
  el alcance de este proyecto (el frontend no tiene credenciales que
  proteger), pero en un despliegue productivo debería restringirse al
  dominio real del frontend.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

La suite cubre:
- **CRUD** (`tests/test_crud_productos.py`): camino feliz de las 5
  operaciones (crear, listar, obtener, actualizar, eliminar) y sus 404.
- **Validaciones** (`tests/test_validaciones_productos.py`): campos
  obligatorios, valores negativos, longitudes, tipos inválidos, y en
  particular que el PUT nunca modifique `stock_actual` aunque el cliente
  intente enviarlo.
- **Baja lógica** (`tests/test_baja_logica_productos.py`): comportamiento
  de `DELETE` con y sin movimientos simulados, filtro `incluir_inactivos`.
- **Datos de prueba** (`tests/test_seed_productos.py`): generación de
  lotes, límites de cantidad (1-200), y el flag `HABILITAR_SEED_ENDPOINT`.
- **Integración** (`tests/test_integracion.py`): flujos completos
  encadenados (crear → editar → listar → eliminar) y healthcheck.

Cada test corre contra una base SQLite en memoria aislada (no comparte
estado entre tests, no toca el filesystem ni la base real del contenedor).
