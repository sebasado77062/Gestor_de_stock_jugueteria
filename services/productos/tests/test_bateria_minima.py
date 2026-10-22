"""
Batería mínima de tests (Paradigmas 3) + Prueba de Carga y Blindaje de Endpoints.

Seis tests, uno por cada capa que se pide demostrar sobre el proyecto real:

    1. Test unitario puro        -> regla de negocio crítica, aislada de HTTP.
    2. Test de integración (201) -> petición HTTP con payload válido + schema JSON.
    3. Test de borde (400)       -> datos corruptos rechazados con 400, nunca 500.
    4. Test de carga concurrente -> 20 peticiones a la vez; mide latencia media
                                    y tasa de éxito.
    5. Sanitización y error 400  -> caracteres maliciosos rechazados con
                                    400 Bad Request, nunca con 500.
    6. Headers de seguridad      -> anti-sniffing y anti-clickjacking en TODA
                                    respuesta, incluidos los errores.

Se corre en vivo con:

    cd services/productos
    pytest tests/test_bateria_minima.py -v

Reutiliza las fixtures de tests/conftest.py (base SQLite en memoria, nueva en
cada test; autenticación simulada como admin), por lo que no necesita Docker,
Redis ni RabbitMQ. El test de carga suma dos fixtures propias (más abajo)
porque necesita una base en archivo que varios hilos puedan usar a la vez.
"""
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from statistics import mean
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.jwt_auth import obtener_usuario_actual
from app.database import db as db_module
from app.database.db import Base, get_db
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


# ===========================================================================
# PRUEBA DE CARGA Y BLINDAJE DE ENDPOINTS
# ===========================================================================
URL_PRODUCTOS = "/api/v1/productos"


def _cantidad_de_productos(client) -> int:
    return len(client.get(URL_PRODUCTOS).json())


# ---------------------------------------------------------------------------
# 4. TEST DE CARGA CONCURRENTE
# 20 clientes venden una unidad del mismo producto en el mismo instante (una
# barrera los libera a todos juntos): PATCH /ajustar-stock, el punto de mayor
# contención del sistema. Se mide la latencia de cada uno y la tasa de éxito.
# Hay stock para los 20, así que todos deben salir bien y el stock terminar en 0.
#
# Fixtures propias: la base en memoria de conftest comparte UNA conexión entre
# todos los hilos, algo que no soporta peticiones simultáneas reales. Acá se usa
# un archivo SQLite temporal y una sesión nueva por request (como en producción).
# ---------------------------------------------------------------------------
@pytest.fixture()
def session_factory_archivo():
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()
    engine = create_engine(
        f"sqlite:///{archivo.name}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)

    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)

    engine.dispose()
    os.remove(archivo.name)


@pytest.fixture()
def client_concurrente(session_factory_archivo, monkeypatch):
    # El arranque de la app llamaría a create_all sobre la base real: se anula,
    # igual que en conftest.py.
    monkeypatch.setattr(db_module.Base.metadata, "create_all", lambda *a, **k: None)

    def override_get_db():
        db = session_factory_archivo()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[obtener_usuario_actual] = lambda: {
        "sub": "1", "email": "admin@test.com", "rol": "admin",
        "tipo": "access", "jti": "jti-fake-admin",
    }

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_carga_20_peticiones_concurrentes(client_concurrente, session_factory_archivo, capsys):
    cantidad = 20

    db = session_factory_archivo()
    producto = Producto(
        nombre="Auto a fricción", precio_venta=2500.0,
        stock_actual=cantidad, stock_minimo=2,
    )
    db.add(producto)
    db.commit()
    id_producto = producto.id_producto
    db.close()

    barrera = threading.Barrier(cantidad, timeout=10)

    def disparar(_):
        barrera.wait()  # todos arrancan a la vez
        inicio = time.perf_counter()
        respuesta = client_concurrente.patch(
            f"{URL_PRODUCTOS}/{id_producto}/ajustar-stock", json={"delta": -1}
        )
        return respuesta.status_code, time.perf_counter() - inicio

    with ThreadPoolExecutor(max_workers=cantidad) as pool:
        resultados = list(pool.map(disparar, range(cantidad)))

    codigos = [codigo for codigo, _ in resultados]
    latencias_ms = [segundos * 1000 for _, segundos in resultados]
    exitosas = codigos.count(200)
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

    # Ninguna venta se perdió ni se duplicó: el stock bajó exactamente 20.
    db = session_factory_archivo()
    stock_final = db.get(Producto, id_producto).stock_actual
    db.close()
    assert stock_final == 0


# ---------------------------------------------------------------------------
# 5. SANITIZACIÓN Y ERROR 400
# Se inyectan caracteres maliciosos (XSS, SQL injection, plantillas, comandos,
# byte nulo, saltos de línea) en cada campo de texto. El servidor debe responder
# 400 con el formato de error del proyecto, nunca 500, y no guardar nada.
# ---------------------------------------------------------------------------
CARGAS_MALICIOSAS = [
    ("xss_en_nombre", {"nombre": "<script>alert(1)</script>"}),
    ("xss_img_onerror_en_marca", {"marca": "<img src=x onerror=alert(1)>"}),
    ("sqli_drop_table_en_categoria", {"categoria": "'; DROP TABLE productos;--"}),
    ("sqli_or_en_nombre", {"nombre": "' OR '1'='1"}),
    ("plantilla_en_nombre", {"nombre": "{{7*7}}"}),
    ("jndi_en_marca", {"marca": "${jndi:ldap://x/a}"}),
    ("byte_nulo_en_nombre", {"nombre": "Muñeca\u0000"}),
    ("salto_de_linea_en_categoria", {"categoria": "Muñecos\r\nSet-Cookie: x=1"}),
    ("comando_entre_backticks", {"marca": "`rm -rf /`"}),
    ("precio_inyectado", {"precio_venta": "1; DROP TABLE productos"}),
]


@pytest.mark.parametrize(
    "malicioso",
    [carga for _, carga in CARGAS_MALICIOSAS],
    ids=[nombre for nombre, _ in CARGAS_MALICIOSAS],
)
def test_sanitizacion_caracteres_maliciosos_devuelven_400_y_no_500(
    client, producto_payload, malicioso
):
    respuesta = client.post(URL_PRODUCTOS, json={**producto_payload, **malicioso})

    assert respuesta.status_code == 400, respuesta.text
    assert respuesta.status_code != 500
    assert respuesta.json()["error"] is True

    # Lo rechazado no quedó guardado, y la tabla sigue intacta y consultable.
    assert _cantidad_de_productos(client) == 0


def test_sanitizacion_tambien_rechaza_en_la_edicion(client, producto_payload):
    creado = client.post(URL_PRODUCTOS, json=producto_payload).json()

    # La edición no acepta stock_actual (ver ProductoUpdate), por eso se lo quita.
    datos_edicion = {k: v for k, v in producto_payload.items() if k != "stock_actual"}
    datos_edicion["nombre"] = "<script>alert(1)</script>"
    respuesta = client.put(f"{URL_PRODUCTOS}/{creado['id_producto']}", json=datos_edicion)

    assert respuesta.status_code == 400
    assert client.get(f"{URL_PRODUCTOS}/{creado['id_producto']}").json()["nombre"] == producto_payload["nombre"]


def test_sanitizacion_no_rechaza_nombres_reales(client, producto_payload):
    """Control: el filtro no puede ser tan estricto que rechace productos legítimos."""
    nombres_reales = [
        "Muñeca Bebota 45cm",
        "Autito 1/64 (Hot Wheels®)",
        "Pack x2 - Edición #3: 50% off!",
        "Bicicleta 16\"",
    ]
    for nombre in nombres_reales:
        respuesta = client.post(URL_PRODUCTOS, json={**producto_payload, "nombre": nombre})
        assert respuesta.status_code == 201, nombre

    marca_con_apostrofe = {**producto_payload, "marca": "O'Brien & Hijos"}
    assert client.post(URL_PRODUCTOS, json=marca_con_apostrofe).status_code == 201


# ---------------------------------------------------------------------------
# 6. HEADERS DE SEGURIDAD
# nosniff evita que el navegador "adivine" el tipo de contenido (sniffing);
# X-Frame-Options (y su equivalente moderno frame-ancestors) impide embeber la
# respuesta en un iframe ajeno (clickjacking). Deben estar en TODA respuesta.
# ---------------------------------------------------------------------------
def _verificar_encabezados(respuesta, contexto):
    assert respuesta.headers.get("x-content-type-options") == "nosniff", contexto
    assert respuesta.headers.get("x-frame-options") == "DENY", contexto
    assert "frame-ancestors 'none'" in respuesta.headers.get("content-security-policy", ""), contexto


def test_encabezados_de_seguridad_en_todas_las_respuestas(client, producto_payload):
    _verificar_encabezados(client.get("/health"), "200 /health")
    _verificar_encabezados(client.get(URL_PRODUCTOS), "200 listado")
    _verificar_encabezados(client.post(URL_PRODUCTOS, json=producto_payload), "201 creación")
    _verificar_encabezados(client.get(f"{URL_PRODUCTOS}/999999"), "404 producto inexistente")
    _verificar_encabezados(client.post(URL_PRODUCTOS, json={}), "400 validación")


def test_encabezados_de_seguridad_tambien_en_el_error_500(client):
    # Se fuerza una falla interna inesperada. raise_server_exceptions=False hace
    # que el TestClient devuelva el 500 en vez de relanzar la excepción. Sin "with":
    # no se ejecuta el arranque de la app (ya lo cubre la fixture client).
    with patch.object(
        producto_service, "obtener_productos", side_effect=RuntimeError("falla simulada")
    ):
        respuesta = TestClient(app, raise_server_exceptions=False).get(URL_PRODUCTOS)

    assert respuesta.status_code == 500
    assert respuesta.json() == {"error": True, "mensaje": "Error interno del servidor."}
    _verificar_encabezados(respuesta, "500")
