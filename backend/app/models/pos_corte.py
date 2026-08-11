"""Corte de caja del POS — un turno de un usuario (fondo inicial → arqueo)."""
from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from ..core.db import Base
from .base import tenant_fk, uuid_pk


class PosCorte(Base):
    __tablename__ = "pos_cortes"

    id = uuid_pk()
    tenant_id = tenant_fk()
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    estado = Column(String(10), nullable=False, server_default="ABIERTO")  # ABIERTO | CERRADO
    fondo_inicial = Column(Numeric(18, 4), nullable=False, server_default="0")
    efectivo_contado = Column(Numeric(18, 4))
    abierto_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    cerrado_at = Column(DateTime(timezone=True))
    notas = Column(Text)
