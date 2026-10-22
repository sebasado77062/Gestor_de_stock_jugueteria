"""
Tests de la baja lógica de productos (issue #6).

Un producto con movimientos asociados no se borra físicamente: se marca
activo=False. "Tiene movimientos" se resuelve consultando al servicio de
Movimientos (app/clients/movimientos_client.py); en estos tests esa consulta
se reemplaza por el stub `movimientos_stub` (ver conftest.py), así que se
puede simular un producto con o sin historial sin levantar Movimientos.
"""


def test_eliminar_producto_con_movimientos_no_lo_borra_lo_marca_inactivo(
    client, producto_payload, movimientos_stub
):
    movimientos_stub.cantidad = 3

    creado = client.post("/api/v1/productos", json=producto_payload).json()

    resp = client.delete(f"/api/v1/productos/{creado['id_producto']}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["activo"] is False
    assert data["id_producto"] == creado["id_producto"]


def test_producto_dado_de_baja_sigue_siendo_consultable_por_id(
    client, producto_payload, movimientos_stub
):
    movimientos_stub.cantidad = 3
    creado = client.post("/api/v1/productos", json=producto_payload).json()
    client.delete(f"/api/v1/productos/{creado['id_producto']}")

    resp = client.get(f"/api/v1/productos/{creado['id_producto']}")

    assert resp.status_code == 200
    assert resp.json()["activo"] is False


def test_producto_dado_de_baja_no_aparece_en_listado_por_defecto(
    client, producto_payload, movimientos_stub
):
    movimientos_stub.cantidad = 3
    creado = client.post("/api/v1/productos", json=producto_payload).json()
    client.delete(f"/api/v1/productos/{creado['id_producto']}")

    resp = client.get("/api/v1/productos")

    assert resp.status_code == 200
    assert resp.json() == []


def test_producto_dado_de_baja_aparece_con_incluir_inactivos(
    client, producto_payload, movimientos_stub
):
    movimientos_stub.cantidad = 3
    creado = client.post("/api/v1/productos", json=producto_payload).json()
    client.delete(f"/api/v1/productos/{creado['id_producto']}")

    resp = client.get("/api/v1/productos?incluir_inactivos=true")

    assert resp.status_code == 200
    ids = [p["id_producto"] for p in resp.json()]
    assert creado["id_producto"] in ids


def test_listado_con_incluir_inactivos_tambien_muestra_los_activos(
    client, producto_payload, movimientos_stub
):
    """incluir_inactivos=true amplía el listado, no lo reemplaza: los
    productos activos también deben seguir apareciendo."""
    movimientos_stub.cantidad = 3
    inactivo = client.post("/api/v1/productos", json=producto_payload).json()
    client.delete(f"/api/v1/productos/{inactivo['id_producto']}")

    movimientos_stub.cantidad = 0
    activo = client.post("/api/v1/productos", json={**producto_payload, "nombre": "Otro activo"}).json()

    resp = client.get("/api/v1/productos?incluir_inactivos=true")

    ids = [p["id_producto"] for p in resp.json()]
    assert inactivo["id_producto"] in ids
    assert activo["id_producto"] in ids


def test_eliminar_producto_sin_movimientos_lo_borra_fisicamente(
    client, producto_payload, movimientos_stub
):
    """Caso de control: con el stub por defecto (sin movimientos), el
    comportamiento sigue siendo el borrado físico de siempre."""
    movimientos_stub.cantidad = 0
    creado = client.post("/api/v1/productos", json=producto_payload).json()

    resp = client.delete(f"/api/v1/productos/{creado['id_producto']}")

    assert resp.status_code == 204

    resp_incluir_inactivos = client.get("/api/v1/productos?incluir_inactivos=true")
    ids = [p["id_producto"] for p in resp_incluir_inactivos.json()]
    assert creado["id_producto"] not in ids
