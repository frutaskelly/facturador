"""Sucursales (plazas) y overrides de precio.

Una `Sucursal` es una unidad de negocio DEL TENANT — Pachuca, Tabasco — no una
propiedad del cliente (rediseño 01-sep-2026: antes llevaba `cliente_id` y cada
cliente duplicaba la plaza). Qué clientes se surten de ella vive en
`ClienteSucursal`, y es ese vínculo el que carga la serie de folios de la
relación: EHMO factura en Tabasco con ZEHMOVH mientras Balles y Jubran comparten
ZHGO en Pachuca. El almacén sí es de la plaza: de ahí sale la mercancía de todos.

Qué lista de precios le toca a quién no se guarda aquí: vive en
`ListaAsignacion` (modelo `precio.py`). Un `PrecioOverride` fija el precio de UN
producto para un cliente, una plaza, o la combinación de ambos, y le gana a
cualquier lista.
"""
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..core.db import Base
from .base import SoftDeleteMixin, TimestampMixin, tenant_fk, uuid_pk


class Sucursal(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "sucursales"

    id = uuid_pk()
    tenant_id = tenant_fk()
    codigo = Column(String(20))
    nombre = Column(String(254), nullable=False)
    domicilio = Column(JSONB, nullable=False, server_default="{}")
    contacto = Column(String(254))
    telefono = Column(String(20))
    activo = Column(Boolean, nullable=False, server_default="true")

    # De dónde sale la mercancía de esta plaza (para TODOS sus clientes).
    # NULL = el almacén default del negocio.
    almacen_id = Column(UUID(as_uuid=True), ForeignKey("almacenes.id", ondelete="SET NULL"))


class ClienteSucursal(Base, TimestampMixin):
    """El vínculo cliente ↔ plaza: este cliente se surte de esta sucursal.

    La serie de folios es DE LA RELACIÓN, no de la plaza ni del cliente a secas
    — es el nivel al que el negocio folia (EHMO×Tabasco → ZEHMOVH). NULL =
    resuelve el siguiente escalón (serie del cliente → default del negocio).
    """
    __tablename__ = "cliente_sucursales"
    __table_args__ = (
        UniqueConstraint("cliente_id", "sucursal_id", name="uq_cliente_sucursal"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False, index=True)
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False, index=True)
    serie_factura_id = Column(UUID(as_uuid=True), ForeignKey("series.id", ondelete="SET NULL"), nullable=True)
    serie_remision_id = Column(UUID(as_uuid=True), ForeignKey("series.id", ondelete="SET NULL"), nullable=True)


class ClienteSucursalSerie(Base):
    """Serie DISPONIBLE en el vínculo cliente×plaza: el ABANICO que ofrecen los
    selectores de emisión (EHMO en Pachuca: ZEHMOHOS hospitales + ZEHMOFAC
    costales). La default sigue en `ClienteSucursal.serie_factura_id` /
    `serie_remision_id`. Sustituye a `sucursal_series` (0056), que colgaba de la
    plaza y con plazas compartidas mezclaría las series de todos los clientes."""
    __tablename__ = "cliente_sucursal_series"
    __table_args__ = (
        UniqueConstraint("cliente_sucursal_id", "serie_id", name="uq_cliente_sucursal_serie"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    cliente_sucursal_id = Column(
        UUID(as_uuid=True), ForeignKey("cliente_sucursales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    serie_id = Column(UUID(as_uuid=True), ForeignKey("series.id", ondelete="CASCADE"), nullable=False)


class PrecioOverride(Base, TimestampMixin):
    __tablename__ = "precio_overrides"
    __table_args__ = (
        CheckConstraint(
            "cliente_id IS NOT NULL OR sucursal_id IS NOT NULL",
            name="ck_override_alguna_dimension",
        ),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    # Al menos uno; ambos = precio de ESE cliente en ESA plaza (el más
    # específico). Solo sucursal = precio de la plaza para todos sus clientes.
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"), index=True)
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="CASCADE"), index=True)
    producto_id = Column(UUID(as_uuid=True), ForeignKey("productos.id", ondelete="CASCADE"), nullable=False, index=True)
    presentacion = Column(String(20), nullable=False, server_default="KILO")
    precio_unitario = Column(Numeric(18, 4), nullable=False)
    vigencia_desde = Column(Date)
    vigencia_hasta = Column(Date)
