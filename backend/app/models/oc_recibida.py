"""Bandeja de órdenes de compra — la puerta de entrada de todo documento.

Cada orden que llega (WhatsApp, correo, captura manual) aterriza aquí ANTES de
convertirse en remisión. La bandeja guarda tres cosas que la remisión no puede
guardar: de dónde vino (canal, remitente, archivo), qué decía el documento
original (`payload`, la evidencia cruda del parseo), y por qué el sistema no
pudo resolverla sola (`motivo`).

Ciclo de vida:
  PENDIENTE  — llegó, pero falta decidir algo (cliente, sucursal, productos).
  ASIGNADA   — ya nació su remisión (`remision_id`).
  DESCARTADA — no procede (duplicado, cotización, documento equivocado).

`origen_externo` es el ancla de idempotencia: el bot manda siempre la misma para
la misma orden, así un reintento por timeout actualiza en vez de duplicar. Sin
esto una caída de red a las 3am dejaba dos remisiones y un folio quemado.
"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from ..core.db import Base
from .base import TimestampMixin, tenant_fk, uuid_pk

CANALES = ("WHATSAPP", "EMAIL", "MANUAL", "API")
ESTADOS = ("PENDIENTE", "ASIGNADA", "DESCARTADA")


class OCRecibida(Base, TimestampMixin):
    __tablename__ = "oc_recibidas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "origen_externo", name="uq_oc_recibida_origen"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    canal = Column(String(20), nullable=False)
    origen_externo = Column(String(120), nullable=False)
    folio_externo = Column(String(60))
    remitente = Column(String(254))
    archivo_nombre = Column(String(254))
    archivo_url = Column(Text)
    recibida_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    estado = Column(String(16), nullable=False, server_default="PENDIENTE")
    motivo = Column(Text)
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="SET NULL"), index=True)
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="SET NULL"))
    resuelto_via = Column(String(16))
    # A dónde se descarga: hospital, plantel, bodega. NO es una sucursal — es un
    # punto DENTRO de una (Balles y Jubran comparten los suyos). Viaja a las
    # observaciones de la remisión y de ahí a las de la factura.
    punto_entrega = Column(String(254))
    ambiguo = Column(Boolean, nullable=False, server_default=text("false"))
    remision_id = Column(UUID(as_uuid=True), ForeignKey("remisiones.id", ondelete="SET NULL"))
    payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    cliente = relationship("Cliente", foreign_keys=[cliente_id])
    sucursal = relationship("Sucursal", foreign_keys=[sucursal_id])
    remision = relationship("Remision", foreign_keys=[remision_id])

    @property
    def cliente_nombre(self):
        return self.cliente.legal_name if self.cliente else None

    @property
    def sucursal_nombre(self):
        return self.sucursal.nombre if self.sucursal else None

    @property
    def remision_folio(self):
        return self.remision.folio_interno if self.remision else None
