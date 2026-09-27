# Arquitectura

## Vista general de servicios

```mermaid
flowchart LR
    subgraph Cliente
        Browser["Navegador"]
    end

    subgraph Docker["docker compose"]
        FE["frontend (nginx)<br/>puerto 8080"]
        PROD["services/productos (FastAPI)<br/>puerto 8000"]
        MOV["services/movimientos<br/>(no implementado)"]
        DB[("SQLite<br/>volumen productos_data")]
    end

    Browser -->|HTTP| FE
    FE -->|"fetch API<br/>(config.js runtime)"| PROD
    PROD --> DB
    MOV -.->|"REST síncrono<br/>(futuro)"| PROD
```

`movimientos` se dibuja punteado porque todavía no existe (ver
`services/movimientos/README.md`); la flecha muestra la comunicación
prevista una vez que se implemente: `movimientos` consultará y actualizará
stock en `productos` vía su API REST, en lugar de acceder directamente a
la base de datos de `productos`.

## Capas dentro de `services/productos`

```mermaid
flowchart TB
    Router["Router<br/>(producto_router.py)<br/>valida HTTP, códigos de estado"]
    Service["Service<br/>(producto_service.py)<br/>reglas de negocio"]
    Model["Model<br/>(producto.py, SQLAlchemy)<br/>persistencia"]
    Schema["Schemas<br/>(producto.py, Pydantic)<br/>contrato de entrada/salida"]

    Router --> Service
    Service --> Model
    Router -.valida body con.-> Schema
```

El router nunca accede a la base de datos directamente: siempre pasa por
`producto_service`. Esto mantiene la lógica de negocio (por ejemplo, "si
tiene movimientos, dar de baja en vez de borrar") en un único lugar,
testeable de forma aislada del framework HTTP.

## Flujo de `DELETE /api/v1/productos/{id}` (issue #6)

```mermaid
sequenceDiagram
    participant C as Cliente
    participant R as Router
    participant S as Service
    participant DB as Base de datos

    C->>R: DELETE /api/v1/productos/{id}
    R->>S: eliminar_producto(id)
    S->>DB: obtener_producto_por_id(id)
    alt Producto no existe
        DB-->>S: None
        S-->>R: None
        R-->>C: 404 Not Found
    else Producto existe
        DB-->>S: Producto
        S->>S: tiene_movimientos_asociados(id)?
        alt Tiene movimientos
            S->>DB: UPDATE activo = false
            S-->>R: Producto (activo=false)
            R-->>C: 200 OK + producto en el body
        else Sin movimientos
            S->>DB: DELETE físico
            S-->>R: Producto (eliminado)
            R-->>C: 204 No Content
        end
    end
```

## Decisión de persistencia

Ver `services/productos/README.md`, sección "Persistencia", para la
justificación de usar SQLite en lugar de un motor cliente-servidor como
Postgres en esta etapa del proyecto, y qué cambiaría para migrar.

## Limitaciones del sistema (resumen)

Cada servicio documenta sus propias limitaciones en detalle en su README.
A nivel de sistema completo, las más relevantes hoy son:

- No hay autenticación ni autorización en ninguna API.
- `services/movimientos` no está implementado: el soft delete de productos
  nunca se activa en la práctica todavía (`tiene_movimientos_asociados`
  siempre devuelve `False`).
- No hay un pipeline de CI configurado que corra los tests automáticamente
  en cada push/PR (los tests existen y pasan localmente, pero ejecutarlos
  hoy es un paso manual).
