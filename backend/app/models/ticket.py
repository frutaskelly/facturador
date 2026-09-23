"""Buzón de tickets — los casos que el bot no se atrevió a resolver solo.

Caso real (23-sep-2026): el bot avisó por WhatsApp «necesito una mano con este
pedido» (Albergue Palenque traía 1 producto y el día ya tenía 20) y el aviso se
perdió en el chat. El dueño pidió un número para retomarlo («revisar ticket 1»)
y poder resolverlo desde WhatsApp O desde el Facturador. Esta tabla es ese
número, con su evidencia y su bitácora.

Quién manda en qué:
  * El NÚMERO lo pone el bot (su `data/tickets.json`): el aviso sale por
    WhatsApp aunque el Facturador esté caído, y el número que el grupo ya leyó
    no puede cambiar cuando el ticket llegue aquí. Único por tenant.
  * El DESENLACE lo aplica el bot, que es quien tiene la foto y la fila de
    proceso. Desde aquí se PIDE una acción (`accion_pedida`); el bot la reclama,
    la ejecuta y la confirma (`accion_tomada_at`). Cerrar a mano sí se cierra
    aquí mismo: no hay nada que ejecutar, solo que el bot deje de perseguirla.

Ciclo de vida:
  ABIERTO   — el bot pidió ayuda y nadie ha decidido.
  EN_CURSO  — alguien pidió una acción y el bot la está aplicando.
  RESUELTO  — el pedido quedó registrado (`resolucion` dice la OC).
  CERRADO   — alguien dijo «ya quedó por otro lado».
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import deferred

from ..core.db import Base
from .base import TimestampMixin, tenant_fk, uuid_pk

TICKET_ESTADOS = ("ABIERTO", "EN_CURSO", "RESUELTO", "CERRADO")
# Lo que se le puede pedir al bot. EXTRA = sumar la foto como complemento
# (lo mismo que reenviarla con EXTRA en el caption); CERRAR = «libro ok».
TICKET_ACCIONES = ("EXTRA", "CERRAR")


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "numero", name="uq_ticket_numero"),
        UniqueConstraint("tenant_id", "origen_externo", name="uq_ticket_origen"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    numero = Column(Integer, nullable=False)
    canal = Column(String(20), nullable=False, server_default="WHATSAPP")
    # Ancla de idempotencia (`WA:<archivo>`): el bot re-sincroniza el mismo
    # ticket cada vez que cambia de estado, y eso actualiza en vez de duplicar.
    origen_externo = Column(String(160), nullable=False)
    estado = Column(String(10), nullable=False, server_default="ABIERTO")
    perfil = Column(String(40))
    grupo = Column(String(160))           # «PEDIDOS FyV HOSPITALES TABASCO»
    jid = Column(String(120))
    remitente = Column(String(160))
    archivo_nombre = Column(String(254))
    nota = Column(Text)                   # el caption original de la foto
    tipo = Column(String(40))             # reemplazo_sospechoso, ilegible, …
    que_paso = Column(Text)               # en cristiano, lo mismo que leyó el grupo
    # Qué acciones tienen sentido para ESTE caso (no todo se arregla con EXTRA).
    acciones = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    foto = deferred(Column(LargeBinary))
    foto_mime = Column(String(40))
    accion_pedida = Column(String(10))
    accion_pedida_por = Column(String(160))
    accion_pedida_at = Column(DateTime(timezone=True))
    accion_tomada_at = Column(DateTime(timezone=True))
    resolucion = Column(Text)
    resuelto_at = Column(DateTime(timezone=True))
    resuelto_por = Column(String(160))
    # Bitácora: [{ts, quien, texto}] — WhatsApp y Facturador escriben en la misma,
    # para que quien lo abra en cualquiera de los dos vea la historia completa.
    eventos = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    recibido_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    @property
    def tiene_foto(self) -> bool:
        # `foto` es deferred: preguntar por ella la cargaría en cada renglón de
        # la lista. El mime se escribe junto con la foto, así que basta.
        return bool(self.foto_mime)
