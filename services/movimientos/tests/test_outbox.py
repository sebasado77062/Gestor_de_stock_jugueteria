"""
Tests del outbox transaccional y del relay que publica en RabbitMQ.

Limitación que cubren: "si RabbitMQ está caído al registrar un movimiento, el
evento se pierde". Con el outbox el evento queda en la base de datos y se
publica cuando el broker vuelve.
"""
import json
import time

import pytest

from app.messaging import outbox
from app.models.outbox import OutboxEvento


def _encolar(SessionLocal, cantidad):
    db = SessionLocal()
    for i in range(1, cantidad + 1):
        outbox.encolar_evento(db, "movimiento.registrado", {"id_movimiento": i})
    db.commit()
    db.close()


def _estado(SessionLocal):
    db = SessionLocal()
    filas = db.query(OutboxEvento).order_by(OutboxEvento.id).all()
    resultado = [(f.id, f.publicado_en is not None, f.intentos, f.ultimo_error) for f in filas]
    db.close()
    return resultado


def test_publica_en_orden_y_marca_los_eventos_como_publicados(SessionLocal, rabbit):
    _encolar(SessionLocal, 3)

    publicados = outbox.publicar_pendientes(SessionLocal, rabbit.abrir)

    assert publicados == 3
    assert [p["id_movimiento"] for _, p in rabbit.publicados] == [1, 2, 3]
    assert all(publicado for _, publicado, _, _ in _estado(SessionLocal))


def test_sin_pendientes_no_abre_conexion_a_rabbitmq(SessionLocal, rabbit):
    assert outbox.publicar_pendientes(SessionLocal, rabbit.abrir) == 0
    assert rabbit.aperturas == 0


def test_no_vuelve_a_publicar_lo_ya_publicado(SessionLocal, rabbit):
    _encolar(SessionLocal, 2)
    outbox.publicar_pendientes(SessionLocal, rabbit.abrir)

    assert outbox.publicar_pendientes(SessionLocal, rabbit.abrir) == 0
    assert len(rabbit.publicados) == 2


def test_con_rabbitmq_caido_el_evento_NO_se_pierde_queda_pendiente(SessionLocal, rabbit):
    _encolar(SessionLocal, 2)
    rabbit.fallar_siempre = True

    with pytest.raises(ConnectionError):
        outbox.publicar_pendientes(SessionLocal, rabbit.abrir)

    assert [publicado for _, publicado, _, _ in _estado(SessionLocal)] == [False, False]


def test_si_no_hay_conexion_el_error_queda_anotado_en_el_evento_mas_antiguo(SessionLocal, rabbit):
    """Para diagnosticar: la fila explica por qué sigue pendiente aunque nunca se llegó a publicar."""
    _encolar(SessionLocal, 2)
    rabbit.fallar_siempre = True

    with pytest.raises(ConnectionError):
        outbox.publicar_pendientes(SessionLocal, rabbit.abrir)
    with pytest.raises(ConnectionError):
        outbox.publicar_pendientes(SessionLocal, rabbit.abrir)

    (_, _, intentos1, error1), (_, _, intentos2, error2) = _estado(SessionLocal)
    assert intentos1 == 2 and "RabbitMQ caído" in error1
    assert (intentos2, error2) == (0, None)


def test_al_publicar_con_exito_se_limpia_el_error_anterior(SessionLocal, rabbit):
    _encolar(SessionLocal, 1)
    rabbit.fallar_siempre = True
    with pytest.raises(ConnectionError):
        outbox.publicar_pendientes(SessionLocal, rabbit.abrir)

    rabbit.fallar_siempre = False
    outbox.publicar_pendientes(SessionLocal, rabbit.abrir)

    [(_, publicado, intentos, error)] = _estado(SessionLocal)
    assert (publicado, intentos, error) == (True, 2, None)


def test_cuando_rabbitmq_vuelve_se_publica_lo_que_quedo_pendiente(SessionLocal, rabbit):
    _encolar(SessionLocal, 2)
    rabbit.fallar_siempre = True
    with pytest.raises(ConnectionError):
        outbox.publicar_pendientes(SessionLocal, rabbit.abrir)

    rabbit.fallar_siempre = False
    publicados = outbox.publicar_pendientes(SessionLocal, rabbit.abrir)

    assert publicados == 2
    assert [p["id_movimiento"] for _, p in rabbit.publicados] == [1, 2]


def test_si_falla_a_mitad_del_lote_se_conserva_el_orden_y_se_registra_el_error(SessionLocal, rabbit):
    _encolar(SessionLocal, 3)
    rabbit.fallar_en = {1}  # la 2.ª publicación del lote falla

    with pytest.raises(ConnectionError):
        outbox.publicar_pendientes(SessionLocal, rabbit.abrir)

    (id1, pub1, int1, err1), (id2, pub2, int2, err2), (id3, pub3, int3, err3) = _estado(SessionLocal)
    assert (pub1, pub2, pub3) == (True, False, False)  # la 3.ª NO se adelanta a la 2.ª
    assert int2 == 1 and "no confirmó" in err2
    assert int3 == 0

    rabbit.fallar_en = set()
    outbox.publicar_pendientes(SessionLocal, rabbit.abrir)
    assert [p["id_movimiento"] for _, p in rabbit.publicados] == [1, 2, 3]  # sin duplicar el 1


def test_el_payload_publicado_es_el_guardado(SessionLocal, rabbit):
    db = SessionLocal()
    outbox.encolar_evento(db, "movimiento.registrado", {"id_movimiento": 9, "stock_actual": 2})
    db.commit()
    db.close()

    outbox.publicar_pendientes(SessionLocal, rabbit.abrir)

    assert rabbit.publicados == [("movimiento.registrado", {"id_movimiento": 9, "stock_actual": 2})]


# ---------------------------------------------------------------------------
# El relay (hilo en segundo plano)
# ---------------------------------------------------------------------------
def _esperar(condicion, timeout=5.0):
    limite = time.time() + timeout
    while time.time() < limite:
        if condicion():
            return True
        time.sleep(0.02)
    return False


def test_el_relay_publica_los_pendientes_al_ser_despertado(SessionLocal, rabbit):
    relay = outbox.OutboxRelay(SessionLocal, rabbit.abrir, intervalo=60)  # sin despertar tardaría 60 s
    relay.iniciar()
    try:
        _encolar(SessionLocal, 2)
        relay.despertar()
        assert _esperar(lambda: len(rabbit.publicados) == 2)
    finally:
        relay.detener()


def test_el_relay_se_recupera_solo_cuando_rabbitmq_vuelve(SessionLocal, rabbit):
    rabbit.fallar_siempre = True
    relay = outbox.OutboxRelay(SessionLocal, rabbit.abrir, intervalo=0.05, intervalo_maximo=0.1)
    relay.iniciar()
    try:
        _encolar(SessionLocal, 1)
        assert _esperar(lambda: rabbit.aperturas >= 2)  # insiste mientras está caído
        assert rabbit.publicados == []

        rabbit.fallar_siempre = False
        assert _esperar(lambda: len(rabbit.publicados) == 1)  # y publica apenas vuelve
    finally:
        relay.detener()


def test_despertar_relay_sin_relay_activo_no_hace_nada():
    outbox.registrar_relay(None)
    outbox.despertar_relay()  # no debe lanzar
