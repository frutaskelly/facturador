"""Nota de crédito (CFDI de egreso) reflejada desde SAE — migración 0086.

SAE las timbra y las aplica en CxC (concepto 1002) contra una o varias
facturas; el conector las trae con sus renglones. El saldo de esas facturas ya
llega descontado por el espejo de facturas, así que aquí nada mueve saldos:
esta tabla es para VER qué se acreditó, a quién y contra qué.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ..core.db import Base
from .base import TimestampMixin, tenant_fk, uuid_pk


class NotaCredito(Base, TimestampMixin):
    __tablename__ = "notas_credito"

    id = uuid_pk()
    tenant_id = tenant_fk()
    cliente_id = Column(
        UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="RESTRICT"), nullable=False
    )
    serie = Column(String(20), nullable=False, server_default="")
    folio = Column(Integer, nullable=False)
    fecha = Column(DateTime(timezone=True), nullable=False)
    total = Column(Numeric(18, 4), nullable=False, server_default="0")
    moneda = Column(String(3), nullable=False, server_default="MXN")
    estado = Column(String(10), nullable=False, server_default="VIGENTE")  # VIGENTE|CANCELADA
    uuid = Column(String(36))
    fecha_cancelacion = Column(DateTime(timezone=True))
    origen = Column(String(12), nullable=False, server_default="ESPEJO_SAE")
    espejo_empresa = Column(String(4))
    espejo_cve_doc = Column(String(30))

    facturas = relationship("NotaCreditoFactura", cascade="all, delete-orphan")


class NotaCreditoFactura(Base):
    __tablename__ = "nota_credito_facturas"

    id = uuid_pk()
    tenant_id = tenant_fk()
    nota_id = Column(
        UUID(as_uuid=True), ForeignKey("notas_credito.id", ondelete="CASCADE"), nullable=False, index=True
    )
    factura_id = Column(UUID(as_uuid=True), ForeignKey("facturas.id", ondelete="SET NULL"))
    factura_ref = Column(String(40))
    importe = Column(Numeric(18, 4), nullable=False)
