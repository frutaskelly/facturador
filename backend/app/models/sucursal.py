"""Sucursales (ship-to) y overrides de precio — precios v2.

Una `Sucursal` pertenece a un cliente. Qué lista de precios le toca ya no se
guarda aquí: vive en `ListaAsignacion` (modelo `precio.py`), junto con las demás
dimensiones de la negociación. Un `PrecioOverride` fija el precio de UN producto
para un cliente o una sucursal, y le gana a cualquier lista.
"""
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..core.db import Base
from .base import SoftDeleteMixin, TimestampMixin, tenant_fk, uuid_pk


class Sucursal(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "sucursales"

    id = uuid_pk()
    tenant_id = tenant_fk()
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False, index=True)
    codigo = Column(String(20))
    nombre = Column(String(254), nullable=False)
    domicilio = Column(JSONB, nullable=False, server_default="{}")
    contacto = Column(String(254))
    telefono = Column(String(20))
    activo = Column(Boolean, nullable=False, server_default="true")

    # Almacén de la sucursal; gana sobre el del cliente (misma prioridad que la
    # serie). NULL = hereda el del cliente.
    almacen_id = Column(UUID(as_uuid=True), ForeignKey("almacenes.id", ondelete="SET NULL"))

    # ── series de folios de la sucursal (ganan sobre las del cliente) ──
    serie_factura_id = Column(UUID(as_uuid=True), ForeignKey("series.id", ondelete="SET NULL"), nullable=True)
    serie_remision_id = Column(UUID(as_uuid=True), ForeignKey("series.id", ondelete="SET NULL"), nullable=True)


class PrecioOverride(Base, TimestampMixin):
    __tablename__ = "precio_overrides"
    __table_args__ = (
        CheckConstraint(
            "(cliente_id IS NOT NULL) <> (sucursal_id IS NOT NULL)",
            name="ck_override_cliente_xor_sucursal",
        ),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    # exactamente uno: precio especial para un cliente O para una sucursal
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"), index=True)
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="CASCADE"), index=True)
    producto_id = Column(UUID(as_uuid=True), ForeignKey("productos.id", ondelete="CASCADE"), nullable=False, index=True)
    presentacion = Column(String(20), nullable=False, server_default="KILO")
    precio_unitario = Column(Numeric(18, 4), nullable=False)
    vigencia_desde = Column(Date)
    vigencia_hasta = Column(Date)
