"""
Tests de consistencia entre Productos (stock) y Movimientos (historial).

Cubren las limitaciones que antes estaban documentadas como conocidas:

1. Atomicidad: el ajuste de stock y el guardado del movimiento no pueden
   quedar a medias. Se prueba qué pasa si Productos falla, si se pierde su
   respuesta, o si falla el guardado final (el "segundo paso").
2. Idempotencia atómica: la misma Idempotency-Key nunca crea dos movimientos
   ni ajusta el stock dos veces, incluso con pedidos simultáneos.
3. Outbox: el evento se guarda junto con la confirmación, y solo entonces.
4. Reconciliación: resuelve los pendientes consultando la verdad, sin volver
   a tocar el stock.

El stock "real" lo lleva ProductosFalso (ver conftest.py), que aplica cada
clave una sola vez, igual que el endpoint real de Productos.
"""
import json
import threading

import pytest

from app.clients.productos_client import ProductosClientError
from app.models.movimiento import ESTADO_CONFIRMADO, ESTADO_PENDIENTE, Movimiento
from app.models.outbox import OutboxEvento
from app.schemas.movimiento import MovimientoCreate
from app.services import movimiento_service
from app.services.movimiento_service import IdempotencyKeyReutilizada
from tests.conftest import TOKEN_FALSO


def venta(cantidad=3, id_producto=1):
    return MovimientoCreate(categoria_movimiento="venta", cantidad_movida=cantidad, id_producto=id_producto)


def movimientos(db):
    return db.query(Movimiento).order_by(Movimiento.id_movimiento).all()


def eventos(db):
    return db.query(OutboxEvento).order_by(OutboxEvento.id).all()


# ---------------------------------------------------------------------------
# Camino feliz y errores definitivos
# ---------------------------------------------------------------------------
def test_camino_feliz_queda_confirmado_con_su_evento_en_el_outbox(SessionLocal, productos):
    db = SessionLocal()

    mov = movimiento_service.registrar_movimiento(db, venta(3), TOKEN_FALSO)

    assert mov.estado == ESTADO_CONFIRMADO
    assert productos.stock[1] == 7
    [evento] = eventos(db)
    assert evento.publicado_en is None  # lo publica el relay, no el request
    payload = json.loads(evento.payload)
    assert payload["id_movimiento"] == mov.id_movimiento
    assert (payload["stock_actual"], payload["stock_minimo"], payload["nombre_producto"]) == (7, 3, "Producto 1")


def test_el_ajuste_usa_la_operacion_id_del_movimiento_como_clave(SessionLocal, productos):
    db = SessionLocal()

    mov = movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO)

    assert mov.operacion_id
    assert productos.llamadas_ajustar == [(1, -3, mov.operacion_id)]


@pytest.mark.parametrize("cantidad,id_producto,codigo", [(99, 1, 409), (1, 777, 404)])
def test_un_rechazo_definitivo_no_deja_movimiento_ni_evento(SessionLocal, productos, cantidad, id_producto, codigo):
    db = SessionLocal()

    with pytest.raises(ProductosClientError) as error:
        movimiento_service.registrar_movimiento(db, venta(cantidad, id_producto), TOKEN_FALSO)

    assert error.value.status_code == codigo
    assert movimientos(db) == []
    assert eventos(db) == []
    assert productos.stock[1] == 10


# ---------------------------------------------------------------------------
# Atomicidad: fallos a mitad de camino
# ---------------------------------------------------------------------------
def test_si_productos_no_responde_queda_pendiente_y_sin_evento(SessionLocal, productos):
    db = SessionLocal()
    productos.caido = True

    with pytest.raises(ProductosClientError) as error:
        movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k1")

    assert error.value.status_code == 503
    [mov] = movimientos(db)
    assert mov.estado == ESTADO_PENDIENTE
    assert eventos(db) == []  # nada se publica de algo no confirmado


def test_el_reintento_con_la_misma_clave_completa_el_pendiente_sin_duplicar(SessionLocal, productos):
    db = SessionLocal()
    productos.caido = True
    with pytest.raises(ProductosClientError):
        movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k1")

    productos.caido = False
    mov = movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k1")

    assert mov.estado == ESTADO_CONFIRMADO
    assert len(movimientos(db)) == 1
    assert len(eventos(db)) == 1
    assert productos.stock[1] == 7


def test_se_pierde_la_respuesta_de_productos_pero_el_stock_ya_cambio(SessionLocal, productos):
    """
    El caso incierto: Productos SÍ descontó, pero la respuesta no llegó. El
    movimiento queda pendiente y el reintento NO descuenta de nuevo (Productos
    reconoce la clave) pero sí confirma el movimiento.
    """
    db = SessionLocal()
    productos.perder_respuesta = True
    with pytest.raises(ProductosClientError):
        movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k1")
    assert productos.stock[1] == 7  # el stock ya cambió...
    assert movimientos(db)[0].estado == ESTADO_PENDIENTE  # ...pero el movimiento no estaba confirmado

    productos.perder_respuesta = False
    mov = movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k1")

    assert productos.stock[1] == 7  # sigue siendo UN descuento
    assert mov.estado == ESTADO_CONFIRMADO
    assert len(eventos(db)) == 1


def test_si_falla_el_guardado_final_el_stock_no_se_pierde_ni_se_descuenta_dos_veces(SessionLocal, productos, monkeypatch):
    """
    El "segundo paso" de la limitación original: el stock se ajustó pero falla
    el guardado en la base de Movimientos. Antes quedaba un stock descontado sin
    movimiento y sin compensación. Ahora el movimiento ya existe como pendiente
    y el reintento lo confirma.
    """
    db = SessionLocal()
    original = movimiento_service._confirmar

    def confirmar_que_falla(*args, **kwargs):
        raise RuntimeError("se cayó la base de datos de movimientos")

    monkeypatch.setattr(movimiento_service, "_confirmar", confirmar_que_falla)
    with pytest.raises(RuntimeError):
        movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k1")
    assert productos.stock[1] == 7
    assert movimientos(db)[0].estado == ESTADO_PENDIENTE  # la intención NO se perdió

    monkeypatch.setattr(movimiento_service, "_confirmar", original)
    mov = movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k1")

    assert mov.estado == ESTADO_CONFIRMADO
    assert productos.stock[1] == 7
    assert len(movimientos(db)) == 1
    assert len(eventos(db)) == 1


def test_un_401_no_descarta_el_movimiento_porque_no_prueba_que_el_stock_no_cambio(SessionLocal, productos):
    db = SessionLocal()
    productos.error_http = ProductosClientError(401, "Tu sesión venció o el token es inválido. Iniciá sesión de nuevo.")

    with pytest.raises(ProductosClientError):
        movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k1")

    assert movimientos(db)[0].estado == ESTADO_PENDIENTE


def test_el_confirmado_y_el_evento_van_en_la_misma_transaccion(SessionLocal, productos, monkeypatch):
    """Si no se puede guardar el evento, el movimiento tampoco queda confirmado."""
    db = SessionLocal()

    def encolar_que_falla(*args, **kwargs):
        raise RuntimeError("no se pudo escribir en el outbox")

    monkeypatch.setattr(movimiento_service.outbox, "encolar_evento", encolar_que_falla)
    with pytest.raises(RuntimeError):
        movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k1")
    db.rollback()

    assert movimientos(db)[0].estado == ESTADO_PENDIENTE
    assert eventos(db) == []


# ---------------------------------------------------------------------------
# Idempotencia (atómica, en la base de datos)
# ---------------------------------------------------------------------------
def test_misma_clave_devuelve_el_mismo_movimiento_y_ajusta_una_sola_vez(SessionLocal, productos):
    db = SessionLocal()

    primero = movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k1")
    segundo = movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k1")

    assert primero.id_movimiento == segundo.id_movimiento
    assert len(productos.llamadas_ajustar) == 1  # el replay confirmado ni siquiera llama a Productos
    assert productos.stock[1] == 7
    assert len(eventos(db)) == 1


def test_claves_distintas_crean_movimientos_distintos(SessionLocal, productos):
    db = SessionLocal()

    a = movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k1")
    b = movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k2")

    assert a.id_movimiento != b.id_movimiento
    assert productos.stock[1] == 4


def test_sin_clave_cada_pedido_es_un_movimiento_nuevo(SessionLocal, productos):
    db = SessionLocal()

    movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO)
    movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO)

    assert len(movimientos(db)) == 2
    assert productos.stock[1] == 4


def test_reutilizar_la_clave_con_otro_contenido_se_rechaza(SessionLocal, productos):
    db = SessionLocal()
    movimiento_service.registrar_movimiento(db, venta(3), TOKEN_FALSO, idempotency_key="k1")

    with pytest.raises(IdempotencyKeyReutilizada):
        movimiento_service.registrar_movimiento(db, venta(5), TOKEN_FALSO, idempotency_key="k1")

    assert productos.stock[1] == 7
    assert len(movimientos(db)) == 1


def test_dos_pedidos_simultaneos_con_la_misma_clave_crean_un_solo_movimiento(SessionLocal, productos):
    """
    El hueco de la idempotencia anterior: "consultar Redis y después guardar" no
    era atómico, así que dos pedidos simultáneos podían ajustar el stock dos
    veces. Ahora la clave es un índice único en la base de datos.
    """
    barrera = threading.Barrier(2)
    resultados, errores = [], []

    def pedido():
        db = SessionLocal()
        try:
            barrera.wait()
            resultados.append(movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key="k-carrera").id_movimiento)
        except Exception as error:  # pragma: no cover - solo para diagnosticar
            errores.append(error)
        finally:
            db.close()

    hilos = [threading.Thread(target=pedido) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    db = SessionLocal()
    assert errores == []
    assert len(set(resultados)) == 1  # ambos recibieron el MISMO movimiento
    assert len(movimientos(db)) == 1
    assert productos.stock[1] == 7  # descontó UNA vez
    assert len(eventos(db)) == 1  # y se encoló UN solo evento
    db.close()


def test_si_dos_pedidos_confirman_el_mismo_movimiento_se_encola_un_solo_evento(SessionLocal, productos):
    """
    Determinista (el test con hilos no siempre logra este entrelazado): dos
    sesiones ven el MISMO movimiento pendiente y ambas intentan confirmarlo.
    La transición pendiente -> confirmado es un UPDATE condicional, así que
    solo una la logra y solo esa encola el evento.
    """
    db_a, db_b = SessionLocal(), SessionLocal()
    mov_a = movimiento_service.iniciar_movimiento(db_a, venta(), "k1")
    mov_b = db_b.get(Movimiento, mov_a.id_movimiento)
    assert (mov_a.estado, mov_b.estado) == (ESTADO_PENDIENTE, ESTADO_PENDIENTE)
    producto = {"id_producto": 1, "nombre": "Producto 1", "stock_actual": 7, "stock_minimo": 3}

    movimiento_service._confirmar(db_a, mov_a, producto)
    movimiento_service._confirmar(db_b, mov_b, producto)

    assert len(eventos(db_a)) == 1
    assert movimientos(db_a)[0].estado == ESTADO_CONFIRMADO


# ---------------------------------------------------------------------------
# Reconciliación (nunca vuelve a tocar el stock)
# ---------------------------------------------------------------------------
def _dejar_pendiente(db, productos, clave, perder_respuesta):
    """Crea un movimiento pendiente: con el stock ya descontado o sin descontar."""
    if perder_respuesta:
        productos.perder_respuesta = True
    else:
        productos.caido = True
    with pytest.raises(ProductosClientError):
        movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO, idempotency_key=clave)
    productos.perder_respuesta = False
    productos.caido = False
    productos.llamadas_ajustar.clear()


def test_reconciliar_confirma_lo_que_si_se_aplico_y_descarta_lo_que_no(SessionLocal, productos):
    db = SessionLocal()
    _dejar_pendiente(db, productos, "aplicado", perder_respuesta=True)  # stock 10 -> 7
    _dejar_pendiente(db, productos, "abandonado", perder_respuesta=False)  # stock no cambió

    resumen = movimiento_service.reconciliar_pendientes(db, TOKEN_FALSO, antiguedad_minima_segundos=0)

    assert resumen == {"revisados": 2, "confirmados": 1, "descartados": 1, "sin_resolver": 0}
    [mov] = movimientos(db)
    assert (mov.idempotency_key, mov.estado) == ("aplicado", ESTADO_CONFIRMADO)
    assert len(eventos(db)) == 1
    assert productos.stock[1] == 7
    assert productos.llamadas_ajustar == []  # reconciliar NO vuelve a ajustar el stock


def test_reconciliar_deja_pendiente_lo_que_no_pudo_consultar(SessionLocal, productos):
    db = SessionLocal()
    _dejar_pendiente(db, productos, "k1", perder_respuesta=True)
    productos.caido = True

    resumen = movimiento_service.reconciliar_pendientes(db, TOKEN_FALSO, antiguedad_minima_segundos=0)

    assert resumen == {"revisados": 1, "confirmados": 0, "descartados": 0, "sin_resolver": 1}
    assert movimientos(db)[0].estado == ESTADO_PENDIENTE


def test_reconciliar_ignora_los_pendientes_recientes(SessionLocal, productos):
    """Un pendiente reciente puede estar siendo completado por un reintento: no se toca."""
    db = SessionLocal()
    _dejar_pendiente(db, productos, "k1", perder_respuesta=True)

    resumen = movimiento_service.reconciliar_pendientes(db, TOKEN_FALSO, antiguedad_minima_segundos=300)

    assert resumen["revisados"] == 0
    assert movimientos(db)[0].estado == ESTADO_PENDIENTE


def test_reconciliar_no_toca_los_movimientos_ya_confirmados(SessionLocal, productos):
    db = SessionLocal()
    movimiento_service.registrar_movimiento(db, venta(), TOKEN_FALSO)

    resumen = movimiento_service.reconciliar_pendientes(db, TOKEN_FALSO, antiguedad_minima_segundos=0)

    assert resumen["revisados"] == 0
    assert len(movimientos(db)) == 1


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def test_contar_movimientos_por_producto_incluye_pendientes(SessionLocal, productos):
    db = SessionLocal()
    movimiento_service.registrar_movimiento(db, venta(1, 1), TOKEN_FALSO)
    _dejar_pendiente(db, productos, "k1", perder_respuesta=True)

    assert movimiento_service.contar_movimientos_por_producto(db, 1) == 2
    assert movimiento_service.contar_movimientos_por_producto(db, 2) == 0
