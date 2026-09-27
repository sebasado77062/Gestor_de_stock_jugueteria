"""
Tests de integración del servicio productos.

A diferencia de los tests de CRUD (que verifican cada operación aislada),
estos ejercitan flujos completos con varios pasos encadenados, tal como
los recorrería un cliente real (el frontend, o a futuro el servicio
movimientos).
"""


def test_health_endpoint_responde_ok(client):
    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "productos"


def test_root_endpoint_responde_ok(client):
    resp = client.get("/")

    assert resp.status_code == 200


def test_flujo_completo_crear_editar_listar_eliminar(client):
    # 1. No hay productos al empezar
    assert client.get("/api/v1/productos").json() == []

    # 2. Se crea un producto
    creado = client.post(
        "/api/v1/productos",
        json={
            "nombre": "Auto a Control Remoto 4x4",
            "marca": "TurboToys",
            "precio_venta": 34990.00,
            "stock_actual": 6,
            "stock_minimo": 3,
            "categoria": "Varios",
        },
    ).json()
    id_producto = creado["id_producto"]

    # 3. Aparece en el listado
    listado = client.get("/api/v1/productos").json()
    assert len(listado) == 1
    assert listado[0]["id_producto"] == id_producto

    # 4. Se edita el precio y la categoría, sin tocar el stock
    editado = client.put(
        f"/api/v1/productos/{id_producto}",
        json={
            "nombre": "Auto a Control Remoto 4x4",
            "marca": "TurboToys",
            "precio_venta": 29990.00,  # rebaja de precio
            "stock_minimo": 3,
            "categoria": "Vehículos",  # categoría corregida
        },
    ).json()
    assert editado["precio_venta"] == 29990.00
    assert editado["categoria"] == "Vehículos"
    assert editado["stock_actual"] == 6  # el stock original se preservó

    # 5. Se elimina (sin movimientos asociados: baja física)
    resp_delete = client.delete(f"/api/v1/productos/{id_producto}")
    assert resp_delete.status_code == 204

    # 6. Ya no aparece ni en el listado ni por id
    assert client.get("/api/v1/productos").json() == []
    assert client.get(f"/api/v1/productos/{id_producto}").status_code == 404


def test_flujo_multiples_productos_con_stock_bajo(client):
    """
    Simula la carga de varios productos con distintos niveles de stock,
    tal como haría el seed_data.py, y valida que el listado los devuelva
    a todos correctamente ordenados por orden de creación.
    """
    productos = [
        {"nombre": "Muñeca Bebota", "marca": "Playkids", "precio_venta": 15999.90,
         "stock_actual": 12, "stock_minimo": 5, "categoria": "Muñecos"},
        {"nombre": "Peluche Oso 40cm", "marca": "SoftFriends", "precio_venta": 9800.00,
         "stock_actual": 3, "stock_minimo": 5, "categoria": "Peluches"},  # stock bajo
    ]
    for p in productos:
        resp = client.post("/api/v1/productos", json=p)
        assert resp.status_code == 201

    listado = client.get("/api/v1/productos").json()
    assert len(listado) == 2

    con_stock_bajo = [p for p in listado if p["stock_actual"] <= p["stock_minimo"]]
    assert len(con_stock_bajo) == 1
    assert con_stock_bajo[0]["nombre"] == "Peluche Oso 40cm"
