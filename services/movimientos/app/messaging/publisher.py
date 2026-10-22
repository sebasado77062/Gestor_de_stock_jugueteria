"""
Publicador del evento de negocio "movimiento.registrado".

Ver services/movimientos/EVENTOS.md para el catálogo completo del evento.

Este módulo solo sabe hablar con RabbitMQ: NO decide cuándo publicar. Quien
publica es el relay del outbox (messaging/outbox.py), que lee los eventos
guardados en la base de datos en la misma transacción que confirmó el
movimiento. El request HTTP ya no publica directamente.

Diseño: el evento es autocontenido. Incluye un snapshot del producto al
momento del movimiento (stock_actual, stock_minimo, nombre), para que los
consumidores no tengan que consultar a Productos por HTTP — lo cual
requeriría JWT de usuario, y un worker asíncrono no tiene uno. El productor
solo conoce el exchange y la routing key; no le importa cuántos
consumidores existan (desacoplamiento).

Garantías de publicación (lo que hace seguro el outbox):

- confirm_delivery: el canal espera la confirmación del broker (publisher
  confirms). Si el broker no confirma, basic_publish lanza una excepción en
  lugar de dar el envío por hecho.
- mandatory=True: si el mensaje no se puede rutear a NINGUNA cola (por ejemplo,
  porque el worker todavía no declaró su cola), el broker lo devuelve y
  basic_publish lanza UnroutableError. Sin esto, RabbitMQ descartaría el
  mensaje en silencio y el outbox lo daría por publicado. Así el evento queda
  pendiente y se reintenta cuando exista un consumidor. Se puede desactivar con
  RABBITMQ_EXIGIR_RUTEO=false (no recomendado).
"""
import json
import logging
import os
from contextlib import contextmanager
from typing import Callable, Iterator

import pika

logger = logging.getLogger(__name__)

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://admin:admin123@localhost:5672/")
EXCHANGE = "stock.events"
ROUTING_KEY = "movimiento.registrado"
EXIGIR_RUTEO = os.getenv("RABBITMQ_EXIGIR_RUTEO", "true").lower() == "true"

# Firma del publicador que usa el outbox: publicar(routing_key, payload).
Publicador = Callable[[str, dict], None]


def construir_payload(movimiento, producto: dict) -> dict:
    """Arma el payload del evento movimiento.registrado (ver EVENTOS.md)."""
    return {
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


def _conectar():
    parametros = pika.URLParameters(RABBITMQ_URL)
    conexion = pika.BlockingConnection(parametros)
    canal = conexion.channel()
    canal.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
    canal.confirm_delivery()
    return conexion, canal


@contextmanager
def abrir_publicador() -> Iterator[Publicador]:
    """
    Abre UNA conexión a RabbitMQ y entrega una función publicar(routing_key,
    payload) para enviar varios eventos por ese mismo canal (el relay publica
    en lotes). Cada mensaje se marca como persistente (delivery_mode=2) para
    sobrevivir a un reinicio del broker, siempre que la cola destino también
    sea durable (ver services/worker-alertas).

    Lanza una excepción si RabbitMQ no está disponible, si no confirma el
    mensaje o si no hay ninguna cola donde rutearlo; el relay del outbox la
    captura y deja el evento pendiente para reintentarlo.
    """
    conexion, canal = _conectar()

    def publicar(routing_key: str, payload: dict) -> None:
        canal.basic_publish(
            exchange=EXCHANGE,
            routing_key=routing_key,
            body=json.dumps(payload),
            properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
            mandatory=EXIGIR_RUTEO,
        )

    try:
        yield publicar
    finally:
        try:
            conexion.close()
        except Exception:  # la conexión pudo haberse caído; no tapar el error original
            logger.debug("No se pudo cerrar limpiamente la conexión a RabbitMQ", exc_info=True)
