"""Devoluciones de mercancía de una remisión CONFIRMADA (el camión ya salió).

Decisión 2026-07-29: la devolución AJUSTA la remisión — las líneas quedan por lo
neto entregado (y la factura posterior sale por lo neto); el inventario regresa
a disponible (ENTRADA_DEVOLUCION) y estas tablas guardan el rastro de qué se
devolvió, cuándo y por qué.
"""
from sqlalchemy import Column, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ..core.db import Base
from .base import TimestampMixin, tenant_fk, uuid_pk


class Devolucion(Base, TimestampMixin):
    __tablename__ = "devoluciones"

    id = uuid_pk()
    tenant_id = tenant_fk()
    remision_id = Column(
        UUID(as_uuid=True), ForeignKey("remisiones.id", ondelete="CASCADE"), nullable=False, index=True
    )
    motivo = Column(Text)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    lineas = relationship("LineaDevolucion", cascade="all, delete-orphan")


class LineaDevolucion(Base):
    __tablename__ = "lineas_devolucion"

    id = uuid_pk()
    tenant_id = tenant_fk()
    devolucion_id = Column(
        UUID(as_uuid=True), ForeignKey("devoluciones.id", ondelete="CASCADE"), nullable=False, index=True
    )
    linea_remision_id = Column(
        UUID(as_uuid=True), ForeignKey("lineas_remision.id", ondelete="SET NULL")
    )
    producto_id = Column(
        UUID(as_uuid=True), ForeignKey("productos.id", ondelete="RESTRICT"), nullable=False
    )
    presentacion = Column(String(20), nullable=False, server_default="KILO")
    # Lo devuelto: en unidades de la presentación de la línea y su equivalente
    # en unidad base (lo que regresó al inventario).
    cantidad = Column(Numeric(18, 4), nullable=False)
    cantidad_base = Column(Numeric(18, 4), nullable=False)
