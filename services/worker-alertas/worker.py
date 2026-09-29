"""
Worker de Alertas de Stock Bajo.

Consume el evento "movimiento.registrado" (ver services/movimientos/EVENTOS.md)
y evalúa si corresponde generar una alerta de stock bajo. Es idempotente por
id_movimiento (ver alertas.py) e implementa reintentos con backoff y una
dead-letter queue (DLQ) final para mensajes que fallan reiteradamente.

Topología de colas (ver también services/worker-alertas/README.md):

    stock.events (exchange topic)  --movimiento.registrado-->  alertas.stock.bajo
                                                                       |
                                                          (nack en error, requeue=False)
                                                                       v
    stock.events.dlx (exchange direct) <--alertas.stock.bajo.retry-- alertas.stock.bajo.retry
                                                                       |
                                                        (TTL de la cola: reintento con backoff)
                                                                       v
    stock.events.dlx --alertas.stock.bajo.reintentar--> alertas.stock.bajo (vuelve a intentarse)

    Tras MAX_REINTENTOS, el mensaje se publica manualmente en:
    stock.events.dlx --alertas.stock.bajo.dlq--> alertas.stock.bajo.dlq (inspección manual)
"""
import json
import logging
import os
import time

import pika

from alertas import (
    evaluar_y_generar_alerta,
    inicializar_db,
    marcar_como_procesado,
    ya_fue_procesado,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker-alertas")

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://admin:admin123@localhost:5672/")
ALERTAS_DB_PATH = os.getenv("ALERTAS_DB_PATH", "/app/data/alertas_procesadas.db")
MAX_REINTENTOS = int(os.getenv("MAX_REINTENTOS", "5"))
RETRY_TTL_MS = int(os.getenv("RETRY_TTL_MS", "5000"))  # backoff fijo de 5s entre reintentos

EXCHANGE_EVENTOS = "stock.events"
EXCHANGE_DLX = "stock.events.dlx"

COLA_PRINCIPAL = "alertas.stock.bajo"
COLA_RETRY = "alertas.stock.bajo.retry"
COLA_DLQ = "alertas.stock.bajo.dlq"

ROUTING_KEY_EVENTO = "movimiento.registrado"
ROUTING_KEY_RETRY = "alertas.stock.bajo.retry"
ROUTING_KEY_REINTENTAR = "alertas.stock.bajo.reintentar"
ROUTING_KEY_DLQ = "alertas.stock.bajo.dlq"


def declarar_topologia(canal):
    """Declara exchanges/colas/bindings de forma idempotente (seguro llamarlo siempre al iniciar)."""
    canal.exchange_declare(exchange=EXCHANGE_EVENTOS, exchange_type="topic", durable=True)
    canal.exchange_declare(exchange=EXCHANGE_DLX, exchange_type="direct", durable=True)

    # Cola principal: si un mensaje se rechaza (nack, requeue=False), Rabbit
    # lo dead-letterea hacia la cola de reintentos.
    canal.queue_declare(
        queue=COLA_PRINCIPAL,
        durable=True,
        arguments={
            "x-dead-letter-exchange": EXCHANGE_DLX,
            "x-dead-letter-routing-key": ROUTING_KEY_RETRY,
        },
    )
    canal.queue_bind(queue=COLA_PRINCIPAL, exchange=EXCHANGE_EVENTOS, routing_key=ROUTING_KEY_EVENTO)
    # También recibe los mensajes que vuelven desde la cola de reintentos.
    canal.queue_bind(queue=COLA_PRINCIPAL, exchange=EXCHANGE_DLX, routing_key=ROUTING_KEY_REINTENTAR)

    # Cola de reintentos: no tiene consumidor propio, solo demora el mensaje
    # (TTL) y lo vuelve a dead-letterear hacia la cola principal.
    canal.queue_declare(
        queue=COLA_RETRY,
        durable=True,
        arguments={
            "x-message-ttl": RETRY_TTL_MS,
            "x-dead-letter-exchange": EXCHANGE_DLX,
            "x-dead-letter-routing-key": ROUTING_KEY_REINTENTAR,
        },
    )
    canal.queue_bind(queue=COLA_RETRY, exchange=EXCHANGE_DLX, routing_key=ROUTING_KEY_RETRY)

    # DLQ final: mensajes que superaron MAX_REINTENTOS, para inspección manual.
    canal.queue_declare(queue=COLA_DLQ, durable=True)
    canal.queue_bind(queue=COLA_DLQ, exchange=EXCHANGE_DLX, routing_key=ROUTING_KEY_DLQ)


def contar_reintentos_previos(properties) -> int:
    """
    RabbitMQ agrega automáticamente el header 'x-death' con un registro por
    cada vez que un mensaje fue dead-letterado. Su longitud es una forma
    confiable de saber cuántas veces ya pasó por el ciclo de reintento.
    """
    headers = properties.headers or {}
    x_death = headers.get("x-death", [])
    return len(x_death)


def procesar_mensaje(canal, method, properties, body, conexion_idempotencia):
    payload = json.loads(body)
    id_movimiento = payload["id_movimiento"]

    if ya_fue_procesado(conexion_idempotencia, id_movimiento):
        logger.info("id_movimiento=%s ya fue procesado antes; se descarta sin reprocesar.", id_movimiento)
        canal.basic_ack(delivery_tag=method.delivery_tag)
        return

    try:
        genero_alerta = evaluar_y_generar_alerta(payload)
        marcar_como_procesado(conexion_idempotencia, id_movimiento, payload["id_producto"], genero_alerta)
        canal.basic_ack(delivery_tag=method.delivery_tag)
    except Exception:
        logger.exception("Error procesando id_movimiento=%s", id_movimiento)
        intentos_previos = contar_reintentos_previos(properties)

        if intentos_previos >= MAX_REINTENTOS:
            logger.error(
                "id_movimiento=%s superó MAX_REINTENTOS=%s; se envía a la DLQ final.",
                id_movimiento, MAX_REINTENTOS,
            )
            canal.basic_publish(
                exchange=EXCHANGE_DLX,
                routing_key=ROUTING_KEY_DLQ,
                body=body,
                properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
            )
            canal.basic_ack(delivery_tag=method.delivery_tag)
        else:
            # requeue=False: NO vuelve a la misma cola directamente, cae por
            # dead-lettering en la cola de reintentos (con el TTL de backoff).
            canal.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


def main():
    conexion_idempotencia = inicializar_db(ALERTAS_DB_PATH)

    parametros = pika.URLParameters(RABBITMQ_URL)
    while True:
        try:
            conexion = pika.BlockingConnection(parametros)
            break
        except pika.exceptions.AMQPConnectionError:
            logger.warning("RabbitMQ no disponible todavía, reintentando en 3s...")
            time.sleep(3)

    canal = conexion.channel()
    declarar_topologia(canal)
    canal.basic_qos(prefetch_count=10)

    def callback(ch, method, properties, body):
        procesar_mensaje(ch, method, properties, body, conexion_idempotencia)

    canal.basic_consume(queue=COLA_PRINCIPAL, on_message_callback=callback)

    logger.info("Worker de Alertas escuchando en la cola '%s'...", COLA_PRINCIPAL)
    try:
        canal.start_consuming()
    except KeyboardInterrupt:
        canal.stop_consuming()
        conexion.close()


if __name__ == "__main__":
    main()
