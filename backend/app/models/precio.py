"""Listas de precios, sus renglones y a qué aplican.

`ListaPrecios` is a named, tenant-scoped collection. `Precio` is one
price for a (producto, presentación, cantidad_minima) — the cantidad_minima
tier lets a single list carry menudeo (qty 1) and mayoreo (higher qty) prices
for the same product.

`ListaAsignacion` dice A QUÉ aplica cada lista — cliente, sucursal, serie o
proyecto, en cualquier combinación. Sustituye a las columnas `lista_precios_id`
que vivían en `clientes` y `sucursales` (migración 0050).

v2 change: `Precio` carries its own `tenant_id` (the RLS key), instead of
relying on a join to `listas_precios` for isolation. Cleaner, uniform policy.
"""
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Date,
    ForeignKey,
    Integer,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

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
    # Espejo de SAE: de qué lista de Aspel se alimenta esta lista — empresa
    # ("02" Pachuca, "03" Tabasco) y CVE_PRECIO de PRECIO_X_PROD. Ambas en
    # NULL = lista manual: el conector no la toca.
    sae_empresa = Column(String(4))
    sae_lista = Column(SmallInteger)


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


class ListaAsignacion(Base, TimestampMixin):
    """A QUÉ aplica una lista de precios: un renglón por negociación.

    Las cuatro dimensiones son opcionales y NULL significa comodín. Gana el
    renglón cuya `especificidad` es mayor — una columna GENERADA por Postgres
    con los pesos (proyecto 8 · serie 4 · sucursal 2 · cliente 1), para que el
    orden de prioridad se declare una sola vez y no en cada consulta.

    Las cuatro en NULL están prohibidas por CHECK: la lista base del negocio ya
    tiene su lugar en `ListaPrecios.es_default`, y dos maneras de decir lo mismo
    terminan discrepando.
    """

    __tablename__ = "lista_asignaciones"
    __table_args__ = (
        CheckConstraint(
            "cliente_id IS NOT NULL OR sucursal_id IS NOT NULL "
            "OR serie_id IS NOT NULL OR proyecto_id IS NOT NULL",
            name="ck_asignacion_alguna_dimension",
        ),
        Index("ix_lista_asignaciones_resolucion", "tenant_id", "especificidad"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    lista_id = Column(
        UUID(as_uuid=True),
        ForeignKey("listas_precios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"))
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="CASCADE"))
    serie_id = Column(UUID(as_uuid=True), ForeignKey("series.id", ondelete="CASCADE"))
    proyecto_id = Column(UUID(as_uuid=True), ForeignKey("proyectos.id", ondelete="CASCADE"))
    vigencia_desde = Column(Date)
    vigencia_hasta = Column(Date)
    notas = Column(Text)
    especificidad = Column(
        SmallInteger,
        Computed(
            "(CASE WHEN proyecto_id IS NOT NULL THEN 8 ELSE 0 END) + "
            "(CASE WHEN serie_id    IS NOT NULL THEN 4 ELSE 0 END) + "
            "(CASE WHEN sucursal_id IS NOT NULL THEN 2 ELSE 0 END) + "
            "(CASE WHEN cliente_id  IS NOT NULL THEN 1 ELSE 0 END)",
            persisted=True,
        ),
        nullable=False,
    )

    lista = relationship("ListaPrecios")
