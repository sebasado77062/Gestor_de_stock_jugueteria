"""
Outbox transaccional: publicación confiable del evento movimiento.registrado.

Flujo:

1. Al CONFIRMAR un movimiento, el servicio guarda el evento en la tabla
   `outbox_eventos` dentro de la misma transacción que cambia el estado a
   `confirmado` (ver services/movimiento_service.py). No hay forma de que
   quede un movimiento confirmado sin su evento, ni un evento de un movimiento
   que no se confirmó.
2. `OutboxRelay` (un hilo de este proceso) toma los eventos sin publicar, los
   envía a RabbitMQ con confirmación del broker y recién entonces los marca
   como publicados. Si RabbitMQ está caído, el evento sigue en la tabla y se
   reintenta con backoff exponencial hasta que el broker vuelva.

Garantía: *at-least-once*. Si el proceso cae justo entre publicar y marcar el
evento, se vuelve a publicar al reiniciar. El consumidor ya es idempotente por
id_movimiento (ver services/worker-alertas), así que un duplicado es inocuo.
"""
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Callable, ContextManager, Optional

from sqlalchemy.orm import Session, sessionmaker

from app.messaging import publisher
from app.models.outbox import OutboxEvento

logger = logging.getLogger(__name__)

LOTE_MAXIMO = 50


def encolar_evento(db: Session, routing_key: str, payload: dict) -> OutboxEvento:
    """
    Agrega el evento al outbox en la sesión actual SIN hacer commit: lo
    confirma quien llama, junto con el cambio de estado del movimiento.
    """
    evento = OutboxEvento(routing_key=routing_key, payload=json.dumps(payload))
    db.add(evento)
    return evento


def _registrar_fallo(db: Session, evento: OutboxEvento, error: Exception) -> None:
    evento.intentos += 1
    evento.ultimo_error = f"{type(error).__name__}: {error}"[:500]
    db.commit()


def publicar_pendientes(
    session_factory: sessionmaker,
    abrir_publicador: Callable[[], ContextManager[publisher.Publicador]] = publisher.abrir_publicador,
    limite: int = LOTE_MAXIMO,
) -> int:
    """
    Publica (en orden) los eventos pendientes y devuelve cuántos se publicaron.

    Si no hay pendientes no abre ninguna conexión a RabbitMQ. Si una
    publicación falla, deja registrado el error en ese evento y RELANZA la
    excepción: se corta el lote para preservar el orden y no insistir contra un
    broker caído. Los eventos ya publicados antes del fallo quedan marcados.
    """
    db = session_factory()
    try:
        pendientes = (
            db.query(OutboxEvento)
            .filter(OutboxEvento.publicado_en.is_(None))
            .order_by(OutboxEvento.id)
            .limit(limite)
            .all()
        )
        if not pendientes:
            return 0

        publicados = 0
        error_registrado = False
        try:
            with abrir_publicador() as publicar:
                for evento in pendientes:
                    try:
                        publicar(evento.routing_key, json.loads(evento.payload))
                    except Exception as error:
                        _registrar_fallo(db, evento, error)
                        error_registrado = True
                        raise
                    evento.intentos += 1
                    evento.ultimo_error = None
                    evento.publicado_en = datetime.now(timezone.utc)
                    db.commit()
                    publicados += 1
        except Exception as error:
            if not error_registrado:
                # Falló antes de poder publicar nada (típicamente: no hubo
                # conexión con RabbitMQ). Se anota en el evento más antiguo para
                # que quien diagnostique vea por qué sigue pendiente.
                _registrar_fallo(db, pendientes[publicados], error)
            raise
        return publicados
    finally:
        db.close()


class OutboxRelay:
    """
    Hilo en segundo plano que vacía el outbox. Duerme `intervalo` segundos entre
    pasadas, o menos si se lo despierta con `despertar()` (se hace al confirmar
    un movimiento, para que el evento salga enseguida). Ante un fallo (por
    ejemplo, RabbitMQ caído) duplica la espera hasta `intervalo_maximo` y la
    restablece apenas una pasada tiene éxito.
    """

    def __init__(
        self,
        session_factory: sessionmaker,
        abrir_publicador: Callable[[], ContextManager[publisher.Publicador]] = publisher.abrir_publicador,
        intervalo: float = 5.0,
        intervalo_maximo: float = 60.0,
    ):
        self._session_factory = session_factory
        self._abrir_publicador = abrir_publicador
        self._intervalo = intervalo
        self._intervalo_maximo = intervalo_maximo
        self._detener = threading.Event()
        self._despertar = threading.Event()
        self._hilo = threading.Thread(target=self._ejecutar, name="outbox-relay", daemon=True)

    def iniciar(self) -> None:
        self._hilo.start()

    def despertar(self) -> None:
        """Pide una pasada inmediata (no bloquea)."""
        self._despertar.set()

    def detener(self, timeout: float = 5.0) -> None:
        self._detener.set()
        self._despertar.set()
        if self._hilo.is_alive():
            self._hilo.join(timeout)

    def _ejecutar(self) -> None:
        espera = self._intervalo
        while not self._detener.is_set():
            try:
                while publicar_pendientes(self._session_factory, self._abrir_publicador) == LOTE_MAXIMO:
                    pass  # había un lote completo: seguir hasta vaciarlo
                espera = self._intervalo
            except Exception as error:
                logger.warning(
                    "Outbox: no se pudo publicar en RabbitMQ (%s: %s). Los eventos quedan pendientes; reintento en %.0fs.",
                    type(error).__name__, error, espera,
                )
                espera = min(espera * 2, self._intervalo_maximo)
            self._despertar.wait(timeout=espera)
            self._despertar.clear()


# Relay activo de este proceso (lo registra el arranque de la app, ver main.py).
# Permite despertarlo desde el router sin acoplarlo a la aplicación. En los
# tests no hay relay y despertar_relay() no hace nada.
_relay_activo: Optional[OutboxRelay] = None


def registrar_relay(relay: Optional[OutboxRelay]) -> None:
    global _relay_activo
    _relay_activo = relay


def despertar_relay() -> None:
    """Pide al relay una pasada inmediata, si hay uno corriendo."""
    if _relay_activo is not None:
        _relay_activo.despertar()
