"""Pagos — cobros del POS (contado por forma de pago) y futuros abonos a crédito."""
from sqlalchemy import Column, Date, DateTime, ForeignKey, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from ..core.db import Base
from .base import tenant_fk, uuid_pk


class Pago(Base):
    __tablename__ = "pagos"

    id = uuid_pk()
    tenant_id = tenant_fk()
    cliente_id = Column(
        UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    remision_id = Column(UUID(as_uuid=True), ForeignKey("remisiones.id", ondelete="SET NULL"))
    corte_id = Column(UUID(as_uuid=True), ForeignKey("pos_cortes.id", ondelete="SET NULL"))
    factura_id = Column(UUID(as_uuid=True), ForeignKey("facturas.id", ondelete="SET NULL"))
    fecha = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    monto = Column(Numeric(18, 4), nullable=False)
    # Forma de pago SAT: 01 efectivo, 03 transferencia, 04 tarjeta, 99 crédito/otros.
    forma_pago = Column(String(5), nullable=False, server_default="01")
    banco = Column(String(100))
    referencia = Column(String(100))
    notas = Column(Text)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
