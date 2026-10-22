"""
Tests del healthcheck HTTP del worker: la lógica de estado (con un reloj
controlado) y el servidor HTTP real, sin necesitar RabbitMQ.
"""
import json
import urllib.error
import urllib.request

import pytest

import health


class RelojFalso:
    def __init__(self):
        self.ahora = 1000.0

    def __call__(self):
        return self.ahora


def test_antes_de_conectar_a_rabbitmq_esta_starting_con_503():
    estado = health.EstadoSalud(tolerancia_segundos=30, reloj=RelojFalso())

    codigo, cuerpo = estado.evaluar()

    assert codigo == 503
    assert cuerpo["status"] == "starting"


def test_consumiendo_con_latido_reciente_esta_ok():
    reloj = RelojFalso()
    estado = health.EstadoSalud(tolerancia_segundos=30, reloj=reloj)
    estado.marcar_consumiendo()
    reloj.ahora += 10

    codigo, cuerpo = estado.evaluar()

    assert (codigo, cuerpo["status"]) == (200, "ok")
    assert cuerpo["service"] == "worker-alertas"


def test_si_el_latido_se_detiene_pasa_a_unhealthy():
    """Detecta un consumidor trabado o una conexión caída: el puerto responde pero ya no late."""
    reloj = RelojFalso()
    estado = health.EstadoSalud(tolerancia_segundos=30, reloj=reloj)
    estado.marcar_consumiendo()
    reloj.ahora += 31

    codigo, cuerpo = estado.evaluar()

    assert (codigo, cuerpo["status"]) == (503, "unhealthy")


def test_un_latido_nuevo_lo_recupera():
    reloj = RelojFalso()
    estado = health.EstadoSalud(tolerancia_segundos=30, reloj=reloj)
    estado.marcar_consumiendo()
    reloj.ahora += 31
    estado.latido()

    assert estado.evaluar()[0] == 200


def test_al_detenerse_vuelve_a_503():
    estado = health.EstadoSalud(tolerancia_segundos=30, reloj=RelojFalso())
    estado.marcar_consumiendo()
    estado.marcar_detenido()

    assert estado.evaluar()[0] == 503


@pytest.fixture()
def servidor():
    estado = health.EstadoSalud(tolerancia_segundos=30)
    srv = health.iniciar_servidor(estado, puerto=0, host="127.0.0.1")  # puerto 0: el SO elige uno libre
    assert srv is not None
    yield estado, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_el_servidor_http_responde_segun_el_estado(servidor):
    estado, base = servidor

    assert _get(f"{base}/health")[0] == 503  # todavía no consume

    estado.marcar_consumiendo()
    codigo, cuerpo = _get(f"{base}/health")
    assert (codigo, cuerpo["status"]) == (200, "ok")


def test_otras_rutas_devuelven_404(servidor):
    _, base = servidor

    assert _get(f"{base}/otra")[0] == 404


def test_si_el_puerto_esta_ocupado_no_rompe_al_worker(servidor):
    estado, base = servidor
    puerto_ocupado = int(base.rsplit(":", 1)[1])

    assert health.iniciar_servidor(estado, puerto=puerto_ocupado, host="127.0.0.1") is None
