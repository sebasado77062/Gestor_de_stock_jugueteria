"""
Modelo OutboxEvento: patrón "transactional outbox".

Publicar el evento en RabbitMQ directamente desde el request tiene un hueco:
si el broker está caído (o el proceso muere) justo después de confirmar el
movimiento, el evento se pierde para siempre. Con el outbox, el evento se
guarda en ESTA base de datos, en la MISMA transacción que confirma el
movimiento: o quedan los dos, o ninguno. Un proceso aparte (ver
messaging/outbox.py) lee los eventos sin publicar y los envía a RabbitMQ,
reintentando hasta que el broker confirma la recepción.

La entrega resultante es *at-least-once* (un evento puede publicarse más de
una vez si el proceso cae entre publicar y marcarlo): por eso el consumidor
es idempotente por id_movimiento (ver services/worker-alertas).
"""
from sqlalchemy import Column, DateTime, Integer, String, Text, func

from app.database.db import Base


class OutboxEvento(Base):
    __tablename__ = "outbox_eventos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    routing_key = Column(String, nullable=False)
    payload = Column(Text, nullable=False)  # JSON del evento, tal como se publica
    creado_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    publicado_en = Column(DateTime(timezone=True), nullable=True, index=True)  # NULL = pendiente de publicar
    intentos = Column(Integer, nullable=False, default=0)
    ultimo_error = Column(Text, nullable=True)
