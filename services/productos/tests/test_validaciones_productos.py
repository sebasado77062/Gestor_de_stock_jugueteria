"""
Tests de validaciones de entrada para /api/v1/productos.

Verifican que los constraints declarados en los schemas Pydantic
(ProductoBase / ProductoCreate / ProductoUpdate) se apliquen correctamente
y que la API responda 400 Bad Request ante datos inválidos (ver el
handler personalizado de RequestValidationError en app/main.py, que
traduce el 422 estándar de FastAPI/Pydantic a 400 con un formato de
error propio del proyecto).
"""
import pytest


@pytest.mark.parametrize(
    "campo_faltante",
    ["nombre", "precio_venta", "stock_minimo"],
)
def test_crear_producto_sin_campo_obligatorio_devuelve_400(client, producto_payload, campo_faltante):
    payload = dict(producto_payload)
    del payload[campo_faltante]

    resp = client.post("/api/v1/productos", json=payload)

    assert resp.status_code == 400


def test_crear_producto_con_precio_negativo_devuelve_400(client, producto_payload):
    payload = {**producto_payload, "precio_venta": -1}

    resp = client.post("/api/v1/productos", json=payload)

    assert resp.status_code == 400


def test_crear_producto_con_stock_actual_negativo_devuelve_400(client, producto_payload):
    payload = {**producto_payload, "stock_actual": -5}

    resp = client.post("/api/v1/productos", json=payload)

    assert resp.status_code == 400


def test_crear_producto_con_stock_minimo_negativo_devuelve_400(client, producto_payload):
    payload = {**producto_payload, "stock_minimo": -1}

    resp = client.post("/api/v1/productos", json=payload)

    assert resp.status_code == 400


def test_crear_producto_con_nombre_vacio_devuelve_400(client, producto_payload):
    payload = {**producto_payload, "nombre": ""}

    resp = client.post("/api/v1/productos", json=payload)

    assert resp.status_code == 400


def test_crear_producto_con_nombre_demasiado_largo_devuelve_400(client, producto_payload):
    payload = {**producto_payload, "nombre": "x" * 121}  # límite es 120

    resp = client.post("/api/v1/productos", json=payload)

    assert resp.status_code == 400


def test_crear_producto_con_precio_como_texto_devuelve_400(client, producto_payload):
    payload = {**producto_payload, "precio_venta": "gratis"}

    resp = client.post("/api/v1/productos", json=payload)

    assert resp.status_code == 400


def test_actualizar_producto_con_stock_actual_en_el_body_lo_ignora(client, producto_payload):
    """
    Núcleo del issue #6: el PUT no debe pisar stock_actual. Si el cliente
    lo incluye en el body de todos modos, el schema ProductoUpdate lo
    descarta silenciosamente (Pydantic ignora campos extra no declarados
    por defecto) y el valor en la base de datos no cambia.
    """
    creado = client.post("/api/v1/productos", json=producto_payload).json()
    stock_original = creado["stock_actual"]

    resp = client.put(
        f"/api/v1/productos/{creado['id_producto']}",
        json={
            "nombre": "Editado",
            "marca": "Playkids",
            "precio_venta": 1.0,
            "stock_minimo": 1,
            "categoria": "Muñecos",
            "stock_actual": 99999,  # intento de pisar el stock
        },
    )

    assert resp.status_code == 200
    assert resp.json()["stock_actual"] == stock_original


def test_actualizar_producto_sin_stock_actual_preserva_el_valor_previo(client, producto_payload):
    """
    Camino normal (sin intento de manipulación): el cliente ni siquiera
    conoce stock_actual porque no está en el schema. El valor debe
    mantenerse igual al que tenía antes del PUT.
    """
    creado = client.post("/api/v1/productos", json=producto_payload).json()

    resp = client.put(
        f"/api/v1/productos/{creado['id_producto']}",
        json={
            "nombre": "Editado sin tocar stock",
            "marca": producto_payload["marca"],
            "precio_venta": producto_payload["precio_venta"],
            "stock_minimo": producto_payload["stock_minimo"],
            "categoria": producto_payload["categoria"],
        },
    )

    assert resp.status_code == 200
    assert resp.json()["stock_actual"] == producto_payload["stock_actual"]
