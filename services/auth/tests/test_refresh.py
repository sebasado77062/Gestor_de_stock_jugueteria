"""
Tests de POST /api/v1/auth/refresh: rotación de un solo uso, y el caso
explícitamente pedido por el issue #7: dos refresh simultáneos con el mismo
token, donde solo uno debe tener éxito.
"""
import threading

import pytest


def test_refresh_exitoso_devuelve_access_token_nuevo(client, admin_token):
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": admin_token["refresh_token"]})

    assert resp.status_code == 200
    assert "access_token" in resp.json()
    # El nuevo refresh token rotado viaja en un header propio (ver
    # auth_router.py) porque el schema de respuesta solo declara access_token.
    assert resp.headers.get("X-New-Refresh-Token")


def test_refresh_rota_el_token_el_nuevo_es_distinto_del_original(client, admin_token):
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": admin_token["refresh_token"]})

    nuevo_refresh = resp.headers["X-New-Refresh-Token"]
    assert nuevo_refresh != admin_token["refresh_token"]


def test_refresh_token_ya_usado_no_puede_reutilizarse(client, admin_token):
    refresh_token = admin_token["refresh_token"]

    primera = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert primera.status_code == 200

    segunda = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert segunda.status_code == 401
    assert "ya fue utilizado" in segunda.json()["mensaje"]


def test_refresh_con_token_invalido_devuelve_401(client):
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": "esto-no-es-un-jwt-valido"})

    assert resp.status_code == 401


def test_refresh_con_access_token_en_vez_de_refresh_devuelve_401(client, admin_token):
    """Un access token no debe servir como refresh token, aunque esté bien firmado."""
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": admin_token["access_token"]})

    assert resp.status_code == 401


def test_nuevo_refresh_token_emitido_es_utilizable(client, admin_token):
    """La rotación no deja al cliente sin forma de seguir refrescando."""
    primera = client.post("/api/v1/auth/refresh", json={"refresh_token": admin_token["refresh_token"]})
    nuevo_refresh = primera.headers["X-New-Refresh-Token"]

    segunda = client.post("/api/v1/auth/refresh", json={"refresh_token": nuevo_refresh})

    assert segunda.status_code == 200


def test_dos_refresh_simultaneos_con_el_mismo_token_solo_uno_tiene_exito(client, admin_token):
    """
    Núcleo del issue #7: "rotación de refresh token de un solo uso (con
    test de dos refresh simultáneos)". Dos requests concurrentes usando el
    MISMO refresh token deben resultar en exactamente un 200 y un 401 (o
    429/lo que corresponda para el perdedor) — nunca dos éxitos, que
    significaría que el mismo refresh token se pudo usar dos veces.
    """
    refresh_token = admin_token["refresh_token"]
    resultados = []
    lock = threading.Lock()

    def intentar_refresh():
        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        with lock:
            resultados.append(resp.status_code)

    hilos = [threading.Thread(target=intentar_refresh) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    exitosos = [r for r in resultados if r == 200]
    fallidos = [r for r in resultados if r == 401]

    assert len(resultados) == 2
    assert len(exitosos) == 1, f"Se esperaba exactamente 1 éxito entre los dos refresh simultáneos, hubo {len(exitosos)} (resultados: {resultados})"
    assert len(fallidos) == 1, f"Se esperaba exactamente 1 fallo entre los dos refresh simultáneos, hubo {len(fallidos)} (resultados: {resultados})"
