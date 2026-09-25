# services/movimientos (placeholder)

Este servicio todavía **no está implementado**. Se reserva el espacio en la
estructura del repo para la futura entidad `Movimiento` (entradas y salidas
de stock), de forma que la organización en microservicios quede definida
desde ahora y la incorporación no requiera reestructurar el proyecto.

## Alcance previsto

- CRUD de movimientos de stock (entrada / salida / ajuste) asociados a un
  `id_producto`.
- Al registrar un movimiento, actualizar el `stock_actual` del producto
  correspondiente.
- Comunicación con `services/productos` vía **API REST síncrona** (HTTP):
  `movimientos` llama a los endpoints de `productos` para leer y actualizar
  stock, en lugar de acceder directamente a su base de datos. Esto mantiene
  la propiedad de los datos dentro de cada servicio.
- Base de datos propia (no comparte tablas con `productos`).

## Por qué no está incluido en `docker-compose.yml` todavía

Agregar un contenedor sin código real generaría un servicio que falla el
build o queda vacío, lo cual rompería el criterio de aceptación
"`docker compose up` levanta todo desde cero". Cuando se implemente el
servicio, se deberá:

1. Agregar `app/`, `requirements.txt` y `Dockerfile` en esta carpeta
   (mismo esquema que `services/productos`).
2. Sumar el servicio `movimientos` a `docker-compose.yml`, con su propio
   healthcheck y variable `PRODUCTOS_API_URL` apuntando al servicio
   `productos` dentro de la red interna de Docker.
