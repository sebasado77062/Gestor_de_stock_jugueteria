"""
Publicador del evento de negocio "movimiento.registrado".

Ver services/movimientos/EVENTOS.md para el catálogo completo del evento.

Diseño: el evento es autocontenido. Incluye un snapshot del producto al
momento del movimiento (stock_actual, stock_minimo, nombre), para que los
consumidores no tengan que consultar a Productos por HTTP — lo cual
requeriría JWT de usuario, y un worker asíncrono no tiene uno. El productor
solo conoce el exchange y la routing key; no le importa cuántos
consumidores existan (desacoplamiento).
"""
import json
import logging
import os

import pika

logger = logging.getLogger(__name__)

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://admin:admin123@localhost:5672/")
EXCHANGE = "stock.events"
ROUTING_KEY = "movimiento.registrado"


def _conectar():
    parametros = pika.URLParameters(RABBITMQ_URL)
    conexion = pika.BlockingConnection(parametros)
    canal = conexion.channel()
    canal.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
    return conexion, canal


def publicar_movimiento_registrado(movimiento, producto: dict) -> None:
    """
    Publica el evento en RabbitMQ. El mensaje se marca como persistente
    (delivery_mode=2) para sobrevivir a un reinicio del broker, siempre que
    la cola destino también sea durable (ver services/worker-alertas).

    Puede lanzar una excepción si RabbitMQ no está disponible; el router
    decide qué hacer con ese error (no se aborta la respuesta 201 al
    cliente por una falla de mensajería: ver routers/movimiento_router.py).
    """
    payload = {
        "id_movimiento": movimiento.id_movimiento,
        "id_producto": movimiento.id_producto,
        "categoria_movimiento": movimiento.categoria_movimiento,
        "cantidad_movida": movimiento.cantidad_movida,
        "fecha_hora": movimiento.fecha_hora.isoformat() if movimiento.fecha_hora else None,
        # Snapshot del producto al momento del evento (post-ajuste):
        "stock_actual": producto.get("stock_actual"),
        "stock_minimo": producto.get("stock_minimo"),
        "nombre_producto": producto.get("nombre"),
    }

    conexion, canal = _conectar()
    try:
        canal.basic_publish(
            exchange=EXCHANGE,
            routing_key=ROUTING_KEY,
            body=json.dumps(payload),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,  # persistente
            ),
        )
        logger.info("Evento movimiento.registrado publicado (id_movimiento=%s)", movimiento.id_movimiento)
    finally:
        conexion.close()