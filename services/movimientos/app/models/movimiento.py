"""
Modelo de la entidad Movimiento.

Capa de Persistencia. Es un registro histórico de SOLO INSERCIONES: no
existen endpoints de UPDATE ni DELETE sobre Movimiento (ver routers). Si en
el futuro hiciera falta "revertir" un movimiento, se documenta como un
nuevo movimiento de reverso, nunca editando o borrando el original: esto
preserva la trazabilidad exigida por la consigna.

Estados (saga de registro, ver services/movimiento_service.py):

- `pendiente`: la intención de movimiento ya está guardada, pero todavía no
  se confirmó que el stock se haya ajustado en Productos. Es un estado
  transitorio: dura lo que tarda la llamada a Productos, o hasta que el
  cliente reintente (con la misma Idempotency-Key) o un admin reconcilie.
- `confirmado`: el stock fue ajustado en Productos y el movimiento es parte
  del historial. Es el estado de todo movimiento ya registrado.

Lo único que cambia sobre un movimiento existente es `estado`, y solo en un
sentido: pendiente -> confirmado. Una intención que Productos rechaza de
forma definitiva (producto inexistente, stock insuficiente) se descarta
porque nunca llegó a ser un movimiento.
"""
from sqlalchemy import Column, DateTime, Index, Integer, String, func

from app.database.db import Base

ESTADO_PENDIENTE = "pendiente"
ESTADO_CONFIRMADO = "confirmado"


class Movimiento(Base):
    __tablename__ = "movimientos"
    __table_args__ = (
        # Únicos pero opcionales (NULL permitido y repetido): en SQLite y
        # Postgres un índice único admite varios NULL. Los nombres se repiten
        # en database/migraciones.py para las bases creadas antes de este cambio.
        Index("ux_movimientos_operacion_id", "operacion_id", unique=True),
        Index("ux_movimientos_idempotency_key", "idempotency_key", unique=True),
    )

    id_movimiento = Column(Integer, primary_key=True, index=True, autoincrement=True)
    fecha_hora = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # venta | ingreso | estropeo | devolucion
    categoria_movimiento = Column(String, nullable=False, index=True)
    cantidad_movida = Column(Integer, nullable=False)

    id_producto = Column(Integer, nullable=False, index=True)
    dni_cliente = Column(String, nullable=True)
    dni_empleado = Column(String, nullable=True)

    # pendiente | confirmado (ver docstring del módulo).
    estado = Column(
        String,
        nullable=False,
        default=ESTADO_CONFIRMADO,
        server_default=ESTADO_CONFIRMADO,
        index=True,
    )

    # Identifica la OPERACIÓN de ajuste de stock en Productos (se envía como
    # Idempotency-Key en el PATCH). Se genera una vez, al guardar la intención,
    # y se reutiliza en cada reintento: así Productos aplica el ajuste una sola
    # vez aunque se lo pidan varias. NULL solo en movimientos anteriores a este
    # mecanismo (ya confirmados).
    operacion_id = Column(String(64), nullable=True)

    # Idempotency-Key enviada por el cliente en el POST (opcional). Al ser una
    # columna única, dos pedidos simultáneos con la misma clave no pueden crear
    # dos movimientos: lo garantiza la base de datos, no un "consultar y luego
    # guardar" en pasos separados.
    idempotency_key = Column(String(100), nullable=True)
