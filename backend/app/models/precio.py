"""Price lists and their line items (tiered pricing).

`ListaPrecios` is a named, tenant-scoped collection. `Precio` is one
price for a (producto, presentación, cantidad_minima) — the cantidad_minima
tier lets a single list carry menudeo (qty 1) and mayoreo (higher qty) prices
for the same product.

v2 change: `Precio` carries its own `tenant_id` (the RLS key), instead of
relying on a join to `listas_precios` for isolation. Cleaner, uniform policy.
"""
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    ForeignKey,
    Integer,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from ..core.db import Base
from .base import SoftDeleteMixin, TimestampMixin, tenant_fk, uuid_pk


class ListaPrecios(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "listas_precios"
    __table_args__ = (
        UniqueConstraint("tenant_id", "codigo", name="uq_lista_tenant_codigo"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    codigo = Column(String(20), nullable=False)
    nombre = Column(String(254), nullable=False)
    status = Column(String(20), nullable=False, server_default="ACTIVO")
    vigencia_desde = Column(Date)
    vigencia_hasta = Column(Date)
    moneda = Column(String(3), nullable=False, server_default="MXN")
    notas = Column(Text)
    # La lista base del negocio (la usan los clientes sin lista propia). La
    # resolución de precios la prefiere sobre la convención codigo='UNICO'.
    es_default = Column(Boolean, nullable=False, server_default="false")


class Precio(Base):
    __tablename__ = "precios"
    __table_args__ = (
        UniqueConstraint(
            "lista_id",
            "producto_id",
            "presentacion",
            "cantidad_minima",
            name="uq_precio_lista_prod_pres_qty",
        ),
        Index("ix_precios_lookup", "lista_id", "producto_id", "presentacion", "cantidad_minima"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    lista_id = Column(
        UUID(as_uuid=True),
        ForeignKey("listas_precios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    producto_id = Column(
        UUID(as_uuid=True),
        ForeignKey("productos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    presentacion = Column(String(20), nullable=False, server_default="KILO")
    precio_unitario = Column(Numeric(18, 4), nullable=False)
    cantidad_minima = Column(Integer, nullable=False, server_default="1")
    vigencia_desde = Column(Date)
    vigencia_hasta = Column(Date)
