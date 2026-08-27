"""Sucursales (ship-to) y overrides de precio — precios v2.

Una `Sucursal` pertenece a un cliente y puede tener su propia lista de precios;
si no, hereda la del cliente. Un `PrecioOverride` fija el precio de un producto
para un cliente o una sucursal específica (lo más específico gana en el
resolutor de precios).
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
from sqlalchemy.orm import relationship

from ..core.db import Base
from .base import SoftDeleteMixin, TimestampMixin, tenant_fk, uuid_pk


class Sucursal(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "sucursales"

    id = uuid_pk()
    tenant_id = tenant_fk()
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False, index=True)
    codigo = Column(String(20))
    nombre = Column(String(254), nullable=False)
    # Lista propia de la sucursal (override de nivel); si NULL hereda la del cliente.
    lista_precios_id = Column(UUID(as_uuid=True), ForeignKey("listas_precios.id", ondelete="SET NULL"))
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

    lista_precios = relationship("ListaPrecios")


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
