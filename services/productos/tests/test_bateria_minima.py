"""
Desafío práctico: Batería Mínima de 3 Tests (Paradigmas 3).

Tres tests, uno por cada capa que se pide demostrar sobre el proyecto real:

    1. Test unitario puro        -> regla de negocio crítica, aislada de HTTP.
    2. Test de integración (201) -> petición HTTP con payload válido + schema JSON.
    3. Test de borde (400)       -> datos corruptos rechazados con 400, nunca 500.

Se corre en vivo con:

    cd services/productos
    pytest tests/test_bateria_minima.py -v

Reutiliza las fixtures de tests/conftest.py (base SQLite en memoria, nueva en
cada test; autenticación simulada como admin), por lo que no necesita Docker,
Redis ni RabbitMQ.
"""
from app.main import app
from app.models.producto import Producto
from app.schemas.producto import ProductoOut
from app.services import producto_service


# ---------------------------------------------------------------------------
# 1. TEST UNITARIO PURO
# Regla de negocio crítica: nunca se puede vender más stock del que hay.
# Se prueba la capa de servicio directamente (sin HTTP, sin autenticación).
# ---------------------------------------------------------------------------
def test_unitario_ajuste_de_stock_rechaza_sobreventa(db_session):
    producto = Producto(
        nombre="Auto a fricción",
        precio_venta=2500.0,
        stock_actual=5,
        stock_minimo=2,
    )
    db_session.add(producto)
    db_session.commit()
    db_session.refresh(producto)
    id_producto = producto.id_producto

    # Pedir 10 unidades cuando hay 5 -> se rechaza y el stock no cambia.
    resultado = producto_service.ajustar_stock_atomico(db_session, id_producto, delta=-10)
    db_session.refresh(producto)
    assert resultado == "sin_stock"
    assert producto.stock_actual == 5

    # Caso límite: pedir exactamente lo que hay es válido y deja el stock en 0.
    resultado = producto_service.ajustar_stock_atomico(db_session, id_producto, delta=-5)
    db_session.refresh(producto)
    assert resultado == "ok"
    assert producto.stock_actual == 0


# ---------------------------------------------------------------------------
# 2. TEST DE INTEGRACIÓN 201
# POST real contra la app FastAPI con un payload válido: debe devolver
# 201 Created y un JSON que respete el schema ProductoOut.
# ---------------------------------------------------------------------------
def test_integracion_crear_producto_devuelve_201_y_schema_json(client, producto_payload):
    respuesta = client.post("/api/v1/productos", json=producto_payload)

    assert respuesta.status_code == 201
    assert respuesta.headers["content-type"].startswith("application/json")

    cuerpo = respuesta.json()

    # El JSON cumple el schema de salida declarado en la API (OpenAPI).
    schema_openapi = app.openapi()["components"]["schemas"]["ProductoOut"]
    assert set(schema_openapi["required"]) <= set(cuerpo.keys())
    ProductoOut.model_validate(cuerpo)  # lanza ValidationError si algún tipo no coincide

    # Y los datos devueltos son los que se enviaron, más los generados por el servidor.
    assert isinstance(cuerpo["id_producto"], int)
    assert cuerpo["nombre"] == producto_payload["nombre"]
    assert cuerpo["precio_venta"] == producto_payload["precio_venta"]
    assert cuerpo["stock_actual"] == producto_payload["stock_actual"]
    assert cuerpo["activo"] is True


# ---------------------------------------------------------------------------
# 3. TEST DE BORDE 400
# Datos corruptos: el servidor debe responder 400 Bad Request con el formato
# de error del proyecto, y nunca caer en un 500.
# ---------------------------------------------------------------------------
def test_borde_datos_corruptos_devuelven_400_y_no_500(client, producto_payload):
    # a) Campos con tipos y valores inválidos al mismo tiempo.
    corrupto = {
        **producto_payload,
        "precio_venta": "abc",   # texto en vez de número
        "stock_actual": -5,      # stock negativo
        "nombre": "",            # nombre vacío
    }
    respuesta = client.post("/api/v1/productos", json=corrupto)
    assert respuesta.status_code == 400
    assert respuesta.status_code != 500
    assert respuesta.json()["error"] is True
    assert len(respuesta.json()["detalles"]) >= 3  # reporta cada campo inválido

    # b) JSON roto (ni siquiera parseable): también 400, no 500.
    respuesta = client.post(
        "/api/v1/productos",
        content='{"nombre": "Muñeca", "precio_venta": ',
        headers={"Content-Type": "application/json"},
    )
    assert respuesta.status_code == 400
    assert respuesta.status_code != 500

    # c) Nada de lo corrupto quedó guardado.
    assert client.get("/api/v1/productos").json() == []
