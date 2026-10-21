"""
Prueba de Carga y Blindaje de Endpoints en Vivo - servicio movimientos.

Tres pruebas, una por cada punto de la consigna, sobre POST /api/v1/movimientos
(el endpoint más pesado del sistema):

    1. Test de carga concurrente -> 20 peticiones a la vez; mide latencia media
                                    y tasa de éxito.
    2. Sanitización y error 400  -> caracteres maliciosos rechazados con
                                    400 Bad Request, nunca con 500.
    3. Headers de seguridad      -> anti-sniffing y anti-clickjacking en TODA
                                    respuesta, incluidos los errores.

Se corre en vivo con:

    cd services/movimientos
    pytest tests/test_bateria_minima.py -v

Reutiliza las fixtures de tests/conftest.py (base SQLite temporal, usuario
autenticado simulado, Productos y RabbitMQ mockeados), por lo que no necesita
Docker, Redis ni los otros servicios.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from statistics import mean
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.movimiento import Movimiento

URL = "/api/v1/movimientos"


def _cantidad_de_movimientos(session_factory) -> int:
    db = session_factory()
    try:
        return db.query(Movimiento).count()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. TEST DE CARGA CONCURRENTE
# 20 clientes disparan el POST en el mismo instante (una barrera los libera a
# todos juntos). Se mide la latencia de cada uno y la tasa de éxito.
# ---------------------------------------------------------------------------
def test_carga_20_peticiones_concurrentes(
    client, session_factory, productos_mock, movimiento_payload, capsys
):
    cantidad = 20
    barrera = threading.Barrier(cantidad, timeout=10)

    def disparar(_):
        barrera.wait()  # todos arrancan a la vez
        inicio = time.perf_counter()
        respuesta = client.post(URL, json=movimiento_payload)
        return respuesta.status_code, respuesta.json(), time.perf_counter() - inicio

    with ThreadPoolExecutor(max_workers=cantidad) as pool:
        resultados = list(pool.map(disparar, range(cantidad)))

    codigos = [codigo for codigo, _, _ in resultados]
    latencias_ms = [segundos * 1000 for _, _, segundos in resultados]
    exitosas = codigos.count(201)
    tasa_exito = exitosas / cantidad * 100

    # El resumen se imprime siempre (no hace falta pytest -s) para mostrarlo en vivo.
    with capsys.disabled():
        print(
            f"\n  [CARGA] {cantidad} peticiones concurrentes -> "
            f"éxito: {tasa_exito:.0f}% ({exitosas}/{cantidad}) | "
            f"latencia media: {mean(latencias_ms):.0f} ms | "
            f"máxima: {max(latencias_ms):.0f} ms"
        )

    assert tasa_exito == 100, f"Códigos recibidos: {sorted(codigos)}"
    assert mean(latencias_ms) < 2000, "La latencia media superó el umbral de 2 s"

    # Ninguna petición se perdió ni se duplicó: 20 movimientos, 20 ids distintos
    # y 20 ajustes de stock pedidos a Productos.
    ids = {cuerpo["id_movimiento"] for _, cuerpo, _ in resultados}
    assert len(ids) == cantidad
    assert _cantidad_de_movimientos(session_factory) == cantidad
    assert productos_mock.call_count == cantidad


# ---------------------------------------------------------------------------
# 2. SANITIZACIÓN Y ERROR 400
# Se inyectan caracteres maliciosos (XSS, SQL injection, path traversal, byte
# nulo, saltos de línea) en cada campo de texto. El servidor debe responder 400
# con el formato de error del proyecto, nunca 500, y no guardar nada.
# ---------------------------------------------------------------------------
CARGAS_MALICIOSAS = [
    ("xss_en_dni", {"dni_cliente": "<script>alert(1)</script>"}),
    ("sqli_corta_en_dni", {"dni_cliente": "' OR '1'='1"}),
    ("sqli_drop_table", {"dni_empleado": "1; DROP TABLE movimientos;--"}),
    ("comilla_y_comentario", {"dni_cliente": "123'--"}),
    ("path_traversal", {"dni_cliente": "../../etc/passwd"}),
    ("byte_nulo", {"dni_empleado": "1234\u0000"}),
    ("salto_de_linea_final", {"dni_cliente": "12345678\n"}),
    ("categoria_inyectada", {"categoria_movimiento": "venta'; DROP TABLE movimientos;--"}),
    ("cantidad_inyectada", {"cantidad_movida": "1 OR 1=1"}),
]


@pytest.mark.parametrize(
    "malicioso",
    [carga for _, carga in CARGAS_MALICIOSAS],
    ids=[nombre for nombre, _ in CARGAS_MALICIOSAS],
)
def test_sanitizacion_caracteres_maliciosos_devuelven_400_y_no_500(
    client, session_factory, productos_mock, movimiento_payload, malicioso
):
    respuesta = client.post(URL, json={**movimiento_payload, **malicioso})

    assert respuesta.status_code == 400, respuesta.text
    assert respuesta.status_code != 500
    assert respuesta.json()["error"] is True

    # El dato rechazado no llegó ni a Productos ni a la base, y la tabla sigue intacta.
    productos_mock.assert_not_called()
    assert _cantidad_de_movimientos(session_factory) == 0


def test_sanitizacion_no_rechaza_datos_validos(client, movimiento_payload):
    """Control: el filtro no puede ser tan estricto que rechace un DNI real."""
    assert client.post(URL, json=movimiento_payload).status_code == 201
    sin_dni = {**movimiento_payload, "dni_cliente": None, "dni_empleado": None}
    assert client.post(URL, json=sin_dni).status_code == 201


# ---------------------------------------------------------------------------
# 3. HEADERS DE SEGURIDAD
# nosniff evita que el navegador "adivine" el tipo de contenido (sniffing);
# X-Frame-Options (y su equivalente moderno frame-ancestors) impide embeber la
# respuesta en un iframe ajeno (clickjacking). Deben estar en TODA respuesta.
# ---------------------------------------------------------------------------
def _verificar_encabezados(respuesta, contexto):
    assert respuesta.headers.get("x-content-type-options") == "nosniff", contexto
    assert respuesta.headers.get("x-frame-options") == "DENY", contexto
    assert "frame-ancestors 'none'" in respuesta.headers.get("content-security-policy", ""), contexto


def test_encabezados_de_seguridad_en_todas_las_respuestas(client, movimiento_payload):
    _verificar_encabezados(client.get("/health"), "200 /health")
    _verificar_encabezados(client.get(URL), "200 listado")
    _verificar_encabezados(client.post(URL, json=movimiento_payload), "201 creación")
    _verificar_encabezados(client.get("/api/v1/ruta-que-no-existe"), "404")
    _verificar_encabezados(client.post(URL, json={}), "400 validación")


def test_encabezados_de_seguridad_tambien_en_el_error_500(client):
    # Se fuerza una falla interna inesperada. raise_server_exceptions=False hace
    # que el TestClient devuelva el 500 en vez de relanzar la excepción.
    with patch(
        "app.services.movimiento_service.listar_movimientos",
        side_effect=RuntimeError("falla simulada"),
    ):
        with TestClient(app, raise_server_exceptions=False) as cliente_500:
            respuesta = cliente_500.get(URL)

    assert respuesta.status_code == 500
    assert respuesta.json() == {"error": True, "mensaje": "Error interno del servidor."}
    _verificar_encabezados(respuesta, "500")
