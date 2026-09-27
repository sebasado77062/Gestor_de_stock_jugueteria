"""
Tests CRUD de /api/v1/productos.

Cubren el camino feliz de cada operación (crear, listar, obtener, actualizar,
eliminar) contra una base de datos aislada por test.
"""


def test_crear_producto_devuelve_201_y_el_producto_creado(client, producto_payload):
    resp = client.post("/api/v1/productos", json=producto_payload)

    assert resp.status_code == 201
    data = resp.json()
    assert data["nombre"] == producto_payload["nombre"]
    assert data["stock_actual"] == producto_payload["stock_actual"]
    assert data["activo"] is True
    assert "id_producto" in data


def test_crear_producto_sin_stock_actual_usa_default_cero(client):
    payload = {
        "nombre": "Producto sin stock inicial",
        "precio_venta": 100.0,
        "stock_minimo": 1,
    }
    resp = client.post("/api/v1/productos", json=payload)

    assert resp.status_code == 201
    assert resp.json()["stock_actual"] == 0


def test_listar_productos_vacio_al_inicio(client):
    resp = client.get("/api/v1/productos")

    assert resp.status_code == 200
    assert resp.json() == []


def test_listar_productos_devuelve_los_creados(client, producto_payload):
    client.post("/api/v1/productos", json=producto_payload)
    client.post("/api/v1/productos", json={**producto_payload, "nombre": "Otro producto"})

    resp = client.get("/api/v1/productos")

    assert resp.status_code == 200
    nombres = [p["nombre"] for p in resp.json()]
    assert nombres == [producto_payload["nombre"], "Otro producto"]


def test_obtener_producto_por_id_existente(client, producto_payload):
    creado = client.post("/api/v1/productos", json=producto_payload).json()

    resp = client.get(f"/api/v1/productos/{creado['id_producto']}")

    assert resp.status_code == 200
    assert resp.json()["id_producto"] == creado["id_producto"]


def test_obtener_producto_inexistente_devuelve_404(client):
    resp = client.get("/api/v1/productos/9999")

    assert resp.status_code == 404
    assert "9999" in resp.json()["mensaje"]


def test_actualizar_producto_existente(client, producto_payload):
    creado = client.post("/api/v1/productos", json=producto_payload).json()

    resp = client.put(
        f"/api/v1/productos/{creado['id_producto']}",
        json={
            "nombre": "Muñeca Bebota (edición)",
            "marca": "Playkids",
            "precio_venta": 16999.90,
            "stock_minimo": 6,
            "categoria": "Muñecos",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["nombre"] == "Muñeca Bebota (edición)"
    assert data["precio_venta"] == 16999.90
    assert data["stock_minimo"] == 6


def test_actualizar_producto_inexistente_devuelve_404(client):
    resp = client.put(
        "/api/v1/productos/9999",
        json={
            "nombre": "No existe",
            "precio_venta": 1.0,
            "stock_minimo": 0,
        },
    )

    assert resp.status_code == 404


def test_eliminar_producto_sin_movimientos_devuelve_204_y_lo_borra(client, producto_payload):
    creado = client.post("/api/v1/productos", json=producto_payload).json()

    resp = client.delete(f"/api/v1/productos/{creado['id_producto']}")
    assert resp.status_code == 204
    assert resp.content == b""

    # El producto ya no existe en absoluto, ni siquiera con incluir_inactivos
    resp_get = client.get(f"/api/v1/productos/{creado['id_producto']}")
    assert resp_get.status_code == 404


def test_eliminar_producto_inexistente_devuelve_404(client):
    resp = client.delete("/api/v1/productos/9999")

    assert resp.status_code == 404
