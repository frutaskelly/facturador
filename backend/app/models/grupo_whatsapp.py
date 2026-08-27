"""Espejo del directorio de grupos del bot.

No es la fuente de verdad —esa sigue siendo `sheets_config.json` en la Mac— sino
la copia que el Facturador necesita para poder MOSTRAR de dónde viene cada orden
y cruzarlo con clientes, sucursales y series. El bot lo sincroniza con su clave
de conexión cada vez que se conecta.
"""
from sqlalchemy import Boolean, Column, DateTime, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB

from ..core.db import Base
from .base import tenant_fk, uuid_pk

ROLES = ("interno", "cliente")


class GrupoWhatsapp(Base):
    __tablename__ = "grupos_whatsapp"
    __table_args__ = (
        UniqueConstraint("tenant_id", "jid", name="uq_grupo_whatsapp_tenant_jid"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    jid = Column(String(120), nullable=False)
    nombre = Column(String(254))
    rol = Column(String(12))
    perfil = Column(String(40))
    activo = Column(Boolean, nullable=False, server_default=text("true"))
    config = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    sincronizado_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
