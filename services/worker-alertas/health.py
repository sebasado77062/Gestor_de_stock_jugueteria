"""
Healthcheck HTTP del worker de Alertas.

El worker es un proceso de background (consume una cola), pero un
orquestador necesita una forma de saber si está VIVO Y CONSUMIENDO, no solo si
el proceso existe. Este módulo levanta, en un hilo aparte, un servidor HTTP
mínimo (solo biblioteca estándar, sin dependencias nuevas) con:

    GET /health -> 200 {"status": "ok", ...}        consumiendo y con latido reciente
                   503 {"status": "starting", ...}  todavía conectando a RabbitMQ
                   503 {"status": "unhealthy", ...} dejó de latir (consumidor trabado)

"Latido": mientras el worker consume, la propia conexión de RabbitMQ ejecuta
cada pocos segundos una tarea (connection.call_later, ver worker.py) que
registra la hora. Esa tarea corre dentro del mismo loop que procesa los
mensajes: si el loop se traba o la conexión se cae, el latido se detiene y el
healthcheck pasa a 503. Un simple "el puerto responde" no detectaría eso.
"""
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

logger = logging.getLogger("worker-alertas.health")

SERVICIO = "worker-alertas"


class EstadoSalud:
    """Estado compartido entre el hilo del consumidor (escribe) y el servidor HTTP (lee)."""

    def __init__(self, tolerancia_segundos: float = 30.0, reloj=time.monotonic):
        self._tolerancia = tolerancia_segundos
        self._reloj = reloj
        self._lock = threading.Lock()
        self._consumiendo = False
        self._ultimo_latido: Optional[float] = None

    def marcar_consumiendo(self) -> None:
        with self._lock:
            self._consumiendo = True
            self._ultimo_latido = self._reloj()

    def marcar_detenido(self) -> None:
        with self._lock:
            self._consumiendo = False

    def latido(self) -> None:
        with self._lock:
            self._ultimo_latido = self._reloj()

    def evaluar(self) -> tuple[int, dict]:
        """Devuelve (codigo_http, cuerpo_json)."""
        with self._lock:
            consumiendo = self._consumiendo
            ultimo = self._ultimo_latido
        if not consumiendo:
            return 503, {"status": "starting", "service": SERVICIO, "detalle": "Sin conexión activa a RabbitMQ."}
        hace = self._reloj() - ultimo
        if hace > self._tolerancia:
            return 503, {
                "status": "unhealthy",
                "service": SERVICIO,
                "detalle": f"Sin latido hace {hace:.0f}s (tolerancia {self._tolerancia:.0f}s).",
            }
        return 200, {"status": "ok", "service": SERVICIO, "ultimo_latido_hace_s": round(hace, 1)}


def _crear_handler(estado: EstadoSalud):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.rstrip("/") == "/health":
                codigo, cuerpo = estado.evaluar()
            else:
                codigo, cuerpo = 404, {"error": True, "mensaje": "No encontrado."}
            datos = json.dumps(cuerpo).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)

        def log_message(self, formato, *args):  # el healthcheck corre cada pocos segundos: no ensuciar el log
            pass

    return Handler


def iniciar_servidor(estado: EstadoSalud, puerto: int, host: str = "0.0.0.0") -> Optional[ThreadingHTTPServer]:
    """
    Levanta el servidor en un hilo daemon y lo devuelve (None si no pudo abrir
    el puerto: el worker sigue funcionando, pero sin healthcheck).
    """
    try:
        servidor = ThreadingHTTPServer((host, puerto), _crear_handler(estado))
    except OSError:
        logger.exception("No se pudo abrir el puerto %s para el healthcheck; el worker sigue sin él.", puerto)
        return None
    threading.Thread(target=servidor.serve_forever, name="health-http", daemon=True).start()
    logger.info("Healthcheck HTTP escuchando en el puerto %s (GET /health).", puerto)
    return servidor
