"""Customers / CRM.

Tenant-scoped. Holds the fiscal identity used to stamp CFDIs (RFC, régimen,
uso CFDI, formas/métodos de pago, domicilio fiscal) plus commercial terms
(credit) and running accumulators.

Series predeterminadas (`serie_factura_id` / `serie_remision_id`) FK a `series`;
fijan la serie del cliente al emitir, salvo que la sucursal o una elección manual
las sobreescriban (ver services/series.py:resolver_serie).
"""
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..core.db import Base
from .base import SoftDeleteMixin, TimestampMixin, tenant_fk, uuid_pk

CLIENTE_TIPO = Enum("PRINCIPAL_GOV", "SUB", "PRIVADO", "OTRO", name="cliente_tipo")


class Cliente(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "clientes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "codigo", name="uq_cliente_tenant_codigo"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    codigo = Column(String(20))
    tipo = Column(CLIENTE_TIPO, nullable=False, server_default="PRIVADO")
    status = Column(String(20), nullable=False, server_default="ACTIVO")

    # ── fiscal identity (CFDI 4.0 receptor) ──
    legal_name = Column(String(254), nullable=False)
    rfc = Column(String(15), nullable=False, index=True)
    regimen_fiscal = Column(String(4))         # RegimenFiscalReceptor
    uso_cfdi_default = Column(String(5))       # UsoCFDI
    forma_pago_default = Column(String(5))     # FormaPago
    metodo_pago_default = Column(String(5))    # MetodoPago (PUE/PPD)
    domicilio_fiscal = Column(JSONB, nullable=False, server_default="{}")

    # ── commercial ──
    # La lista de precios NO se cuelga de aquí: vive en `lista_asignaciones`,
    # que también sabe de sucursal, serie y proyecto (migración 0050).
    condiciones_pago = Column(String(50))
    limite_credito = Column(Numeric(18, 4), nullable=False, server_default="0")
    dias_credito = Column(Integer, nullable=False, server_default="0")
    descuento_default = Column(Numeric(5, 2), nullable=False, server_default="0")
    config_addenda = Column(JSONB, nullable=False, server_default="{}")

    # ── accumulators ──
    saldo_actual = Column(Numeric(18, 4), nullable=False, server_default="0")
    ventas_ytd = Column(Numeric(18, 4), nullable=False, server_default="0")
    ultima_venta_at = Column(DateTime(timezone=True))
    ultimo_pago_at = Column(DateTime(timezone=True))

    custom_fields = Column(JSONB, nullable=False, server_default="{}")

    # ── series de folios predeterminadas del cliente (la sucursal gana sobre esto) ──
    # De qué almacén sale su mercancía. NULL = el predeterminado del inquilino.
    # Varios clientes pueden apuntar al mismo almacén: el enlace es muchos a uno.
    almacen_id = Column(UUID(as_uuid=True), ForeignKey("almacenes.id", ondelete="SET NULL"))
    serie_factura_id = Column(
        UUID(as_uuid=True), ForeignKey("series.id", ondelete="SET NULL"), nullable=True
    )
    serie_remision_id = Column(
        UUID(as_uuid=True), ForeignKey("series.id", ondelete="SET NULL"), nullable=True
    )

    # Candado de la migración (0055): el cliente PARTICIPA en el espejo de SAE
    # (sus reflejos entran y se actualizan; sin el flag, facturar nativo está
    # abierto). OJO desde la 0070: el corte a emisión nativa es POR SERIE
    # (series.espejo_sae) — NO apagues este switch para "completar" un corte
    # parcial: apagarlo rompe el espejo de lo histórico y de las plazas que
    # siguen en SAE (EHMO Tabasco). Se apaga solo cuando el cliente entero
    # dejó SAE y sus reflejos ya no se mueven.
    espejo_sae = Column(Boolean, nullable=False, server_default=text("false"))

