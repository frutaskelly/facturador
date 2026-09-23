"""Recibo de Pago (REP) — Complemento de Pago 2.0 (CFDI tipo P) y su detalle."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ..core.db import Base
from .base import TimestampMixin, tenant_fk, uuid_pk


class ReciboPago(Base, TimestampMixin):
    __tablename__ = "recibos_pago"

    id = uuid_pk()
    tenant_id = tenant_fk()
    cliente_id = Column(
        UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="RESTRICT"), nullable=False
    )
    serie = Column(String(20), nullable=False, server_default="P")
    folio = Column(Integer, nullable=False)
    fecha_pago = Column(DateTime(timezone=True), nullable=False)
    forma_pago = Column(String(5), nullable=False, server_default="03")   # SAT: cómo pagó
    monto = Column(Numeric(18, 4), nullable=False)
    moneda = Column(String(3), nullable=False, server_default="MXN")
    num_operacion = Column(String(100))
    banco = Column(String(100))
    estado = Column(String(10), nullable=False, server_default="BORRADOR")  # BORRADOR|TIMBRADO|CANCELADO
    uuid = Column(String(36))
    facturama_id = Column(String(40))
    xml = Column(Text)
    fecha_timbrado = Column(DateTime(timezone=True))
    fecha_cancelacion = Column(DateTime(timezone=True))
    motivo_cancelacion = Column(String(2))
    notas = Column(Text)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    # NATIVO = lo timbró el Facturador · ESPEJO_SAE = lo timbró SAE y el
    # conector lo refleja (migración 0086). Un espejo NUNCA mueve saldos: el
    # saldo de sus facturas ya llega calculado desde la CxC de SAE.
    origen = Column(String(12), nullable=False, server_default="NATIVO")
    espejo_empresa = Column(String(4))
    espejo_cve_doc = Column(String(30))

    facturas = relationship("ReciboPagoFactura", cascade="all, delete-orphan")


class ReciboPagoFactura(Base):
    __tablename__ = "recibo_pago_facturas"

    id = uuid_pk()
    tenant_id = tenant_fk()
    recibo_id = Column(
        UUID(as_uuid=True), ForeignKey("recibos_pago.id", ondelete="CASCADE"), nullable=False, index=True
    )
    factura_id = Column(
        UUID(as_uuid=True), ForeignKey("facturas.id", ondelete="RESTRICT"), nullable=True
    )
    # Respaldo del espejo: la factura como SAE la nombra, por si no está aquí
    factura_ref = Column(String(40))
    importe_pagado = Column(Numeric(18, 4), nullable=False)
    num_parcialidad = Column(Integer, nullable=False, server_default="1")
    # NULL sólo en el espejo, cuando SAE no dejó rastro del saldo
    saldo_anterior = Column(Numeric(18, 4))
    saldo_insoluto = Column(Numeric(18, 4))
    moneda_dr = Column(String(3), nullable=False, server_default="MXN")

    factura = relationship("Factura")
