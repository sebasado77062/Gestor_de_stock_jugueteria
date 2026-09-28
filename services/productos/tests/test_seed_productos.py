"""
Tests del endpoint de utilidad /api/v1/dev/seed-productos (carga masiva de
datos de prueba). No es parte del CRUD de negocio, pero se cubre igual
porque el frontend depende de que funcione (botón "Cargar datos de prueba").
"""
from app.routers import seed_router


def test_seed_productos_cantidad_por_defecto(client):
    resp = client.post("/api/v1/dev/seed-productos")

    assert resp.status_code == 201
    assert len(resp.json()) == 20  # default


def test_seed_productos_cantidad_personalizada(client):
    resp = client.post("/api/v1/dev/seed-productos?cantidad=5")

    assert resp.status_code == 201
    assert len(resp.json()) == 5


def test_seed_productos_quedan_disponibles_en_el_listado(client):
    client.post("/api/v1/dev/seed-productos?cantidad=7")

    resp = client.get("/api/v1/productos")

    assert len(resp.json()) == 7


def test_seed_productos_genera_datos_con_forma_valida(client):
    resp = client.post("/api/v1/dev/seed-productos?cantidad=10")

    for producto in resp.json():
        assert producto["precio_venta"] >= 0
        assert producto["stock_actual"] >= 0
        assert producto["stock_minimo"] >= 0
        assert producto["nombre"]
        assert producto["activo"] is True


def test_seed_productos_cantidad_fuera_de_rango_devuelve_400(client):
    resp = client.post("/api/v1/dev/seed-productos?cantidad=500")

    assert resp.status_code == 400


def test_seed_productos_cantidad_cero_devuelve_400(client):
    resp = client.post("/api/v1/dev/seed-productos?cantidad=0")

    assert resp.status_code == 400


def test_seed_productos_deshabilitado_devuelve_403(client, monkeypatch):
    monkeypatch.setattr(seed_router, "_HABILITADO", False)

    resp = client.post("/api/v1/dev/seed-productos?cantidad=5")

    assert resp.status_code == 403
