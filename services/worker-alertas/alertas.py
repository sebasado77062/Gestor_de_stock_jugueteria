"""
Lógica de negocio del worker de Alertas, separada del loop de consumo de
RabbitMQ (worker.py) para poder testearla sin necesitar un broker real.

El evento es autocontenido: trae stock_actual, stock_minimo y nombre del
producto al momento del movimiento. El worker no consulta Productos.
"""
import sqlite3

UMBRAL_LOG = "[ALERTA STOCK BAJO]"


def inicializar_db(path_db: str) -> sqlite3.Connection:
    """
    Abre (creando si hace falta) la base local de idempotencia del worker.
    RabbitMQ entrega at-least-once, así que un mismo id_movimiento puede
    llegar más de una vez y hay que evitar reprocesarlo.
    """
    conexion = sqlite3.connect(path_db, check_same_thread=False)
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS procesados (
            id_movimiento INTEGER PRIMARY KEY,
            id_producto INTEGER NOT NULL,
            genero_alerta INTEGER NOT NULL,
            fecha_hora TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conexion.commit()
    return conexion


def ya_fue_procesado(conexion: sqlite3.Connection, id_movimiento: int) -> bool:
    cursor = conexion.execute(
        "SELECT 1 FROM procesados WHERE id_movimiento = ?", (id_movimiento,)
    )
    return cursor.fetchone() is not None


def marcar_como_procesado(conexion: sqlite3.Connection, id_movimiento: int, id_producto: int, genero_alerta: bool) -> None:
    conexion.execute(
        "INSERT OR IGNORE INTO procesados (id_movimiento, id_producto, genero_alerta) VALUES (?, ?, ?)",
        (id_movimiento, id_producto, int(genero_alerta)),
    )
    conexion.commit()


def evaluar_y_generar_alerta(payload: dict) -> bool:
    """
    Evalúa si corresponde generar una alerta de stock bajo (stock_actual <
    stock_minimo), a partir del snapshot incluido en el propio evento.

    Devuelve True si se generó la alerta, False si el stock está OK.
    """
    id_producto = payload["id_producto"]
    stock_actual = payload["stock_actual"]
    stock_minimo = payload["stock_minimo"]

    if stock_actual < stock_minimo:
        print(
            f"{UMBRAL_LOG} producto={payload.get('nombre_producto', id_producto)} "
            f"(id={id_producto}) stock_actual={stock_actual} stock_minimo={stock_minimo} "
            f"-- disparado por movimiento id_movimiento={payload['id_movimiento']}"
        )
        return True
    return False