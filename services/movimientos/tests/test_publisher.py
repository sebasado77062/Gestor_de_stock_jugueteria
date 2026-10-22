"""
Tests del publisher de RabbitMQ con un canal falso (sin broker).

Verifican las garantías que hacen confiable al outbox: confirmación del broker
(confirm_delivery), rechazo de mensajes sin ruta (mandatory) y mensajes
persistentes.
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.messaging import publisher


class CanalFalso:
    def __init__(self):
        self.confirm = False
        self.exchanges = []
        self.publicaciones = []
        self.error_al_publicar = None

    def exchange_declare(self, **kwargs):
        self.exchanges.append(kwargs)

    def confirm_delivery(self):
        self.confirm = True

    def basic_publish(self, **kwargs):
        if self.error_al_publicar:
            raise self.error_al_publicar
        self.publicaciones.append(kwargs)


class ConexionFalsa:
    def __init__(self, canal):
        self._canal = canal
        self.cerrada = False

    def channel(self):
        return self._canal

    def close(self):
        self.cerrada = True


@pytest.fixture()
def broker_falso(monkeypatch):
    canal = CanalFalso()
    conexion = ConexionFalsa(canal)
    monkeypatch.setattr(publisher.pika, "URLParameters", lambda url: url)
    monkeypatch.setattr(publisher.pika, "BlockingConnection", lambda parametros: conexion)
    return SimpleNamespace(canal=canal, conexion=conexion)


def test_activa_la_confirmacion_del_broker_y_declara_el_exchange_durable(broker_falso):
    with publisher.abrir_publicador():
        pass

    assert broker_falso.canal.confirm is True
    assert broker_falso.canal.exchanges == [
        {"exchange": "stock.events", "exchange_type": "topic", "durable": True}
    ]


def test_publica_persistente_y_con_mandatory(broker_falso):
    with publisher.abrir_publicador() as publicar:
        publicar("movimiento.registrado", {"id_movimiento": 1})

    [pub] = broker_falso.canal.publicaciones
    assert pub["exchange"] == "stock.events"
    assert pub["routing_key"] == "movimiento.registrado"
    assert json.loads(pub["body"]) == {"id_movimiento": 1}
    assert pub["properties"].delivery_mode == 2  # persistente
    assert pub["mandatory"] is True  # si no hay cola destino, no se descarta en silencio


def test_publica_varios_eventos_por_la_misma_conexion_y_la_cierra(broker_falso):
    with publisher.abrir_publicador() as publicar:
        publicar("movimiento.registrado", {"id_movimiento": 1})
        publicar("movimiento.registrado", {"id_movimiento": 2})

    assert len(broker_falso.canal.publicaciones) == 2
    assert broker_falso.conexion.cerrada is True


def test_propaga_el_error_del_broker_y_cierra_la_conexion(broker_falso):
    broker_falso.canal.error_al_publicar = publisher.pika.exceptions.UnroutableError([])

    with pytest.raises(publisher.pika.exceptions.UnroutableError):
        with publisher.abrir_publicador() as publicar:
            publicar("movimiento.registrado", {"id_movimiento": 1})

    assert broker_falso.conexion.cerrada is True


def test_el_payload_del_evento_conserva_el_formato_documentado():
    movimiento = SimpleNamespace(
        id_movimiento=123,
        id_producto=45,
        categoria_movimiento="venta",
        cantidad_movida=2,
        fecha_hora=datetime(2026, 9, 27, 14, 32, 10, tzinfo=timezone.utc),
    )
    producto = {"nombre": "Auto a fricción", "stock_actual": 3, "stock_minimo": 5}

    assert publisher.construir_payload(movimiento, producto) == {
        "id_movimiento": 123,
        "id_producto": 45,
        "categoria_movimiento": "venta",
        "cantidad_movida": 2,
        "fecha_hora": "2026-09-27T14:32:10+00:00",
        "stock_actual": 3,
        "stock_minimo": 5,
        "nombre_producto": "Auto a fricción",
    }
