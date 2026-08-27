"""Equivalencias de cliente — cómo se llama este cliente en los otros sistemas.

Es el patrón de `producto_alias` aplicado a clientes: una clave de un sistema
externo resuelve al cliente (y opcionalmente a su sucursal) sin que nadie tenga
que adivinar. Único por (tenant, sistema, clave_normalizada).

`sistema` dice de dónde viene la clave:
  RFC       — el RFC impreso en la orden de compra
  SAE       — '<empresa>:<cliente>' de ASPEL SAE (p. ej. '02:5')
  PROYECTO  — '<perfil>:<PROYECTO>' del bot (p. ej. 'ehmo:HOSPITALES')
  NOMBRE    — la razón social tal como aparece dentro del documento
  UBICACION — '<perfil>:<ubicación>' (hospital/plantel) → cliente + SUCURSAL
  WHATSAPP  — el JID del grupo, solo para grupos de un solo cliente

`confianza` separa lo que un humano confirmó (CONFIRMADA) de lo que el bot
propuso solo (SUGERIDA); el resolutor únicamente usa las CONFIRMADAS, las otras
son la bandeja de revisión.
"""
from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID

from ..core.db import Base
from .base import tenant_fk, uuid_pk

SISTEMAS = ("RFC", "SAE", "PROYECTO", "NOMBRE", "UBICACION", "WHATSAPP")


class ClienteExterno(Base):
    __tablename__ = "cliente_externos"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "sistema", "clave_normalizada",
            name="uq_cliente_externo_tenant_sistema_clave",
        ),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    sistema = Column(String(16), nullable=False)
    clave = Column(String(254), nullable=False)
    clave_normalizada = Column(String(254), nullable=False)
    cliente_id = Column(
        UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="SET NULL"))
    origen = Column(String(12), nullable=False, server_default="MANUAL")        # MANUAL | BOT | IMPORT | IA
    confianza = Column(String(10), nullable=False, server_default="CONFIRMADA")  # CONFIRMADA | SUGERIDA
    notas = Column(Text)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
