"""Series de folios — control interno consecutivo (sin huecos).

Separadas por tipo de documento (FACTURA, NOTA_CREDITO, REMISION, PAGO) y por
naturaleza (FISCAL = CFDI, NO_FISCAL = remisión/nota de venta). El folio se
reserva con un contador bloqueado en la transacción (services/series.py). El
folio interno es distinto del UUID que asigna el PAC al timbrar.
"""
from sqlalchemy import Boolean, Column, Date, Integer, String, Text, UniqueConstraint

from ..core.db import Base
from .base import TimestampMixin, tenant_fk, uuid_pk


class Serie(Base, TimestampMixin):
    __tablename__ = "series"
    __table_args__ = (
        UniqueConstraint("tenant_id", "codigo", "tipo_documento", name="uq_serie_tenant_codigo_doc"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    codigo = Column(String(25), nullable=False)
    tipo = Column(String(10), nullable=False, server_default="FISCAL")        # FISCAL | NO_FISCAL
    tipo_documento = Column(String(20), nullable=False)                        # FACTURA | NOTA_CREDITO | REMISION | PAGO
    nombre = Column(String(120))
    folio_actual = Column(Integer, nullable=False, server_default="0")
    activa = Column(Boolean, nullable=False, server_default="true")
    # Serie predeterminada del inquilino para su tipo_documento (una sola, índice parcial).
    es_default = Column(Boolean, nullable=False, server_default="false")
    # Serie cuyos CFDI se emiten EN SAE y aquí solo se reflejan (espejo). Un
    # cliente en espejo solo factura nativo por una serie con esto apagado:
    # el corte del SAE es por serie/plaza, no por cliente (EHMO: Pachuca
    # nativa, Tabasco espejada).
    espejo_sae = Column(Boolean, nullable=False, server_default="false")
    vigencia_desde = Column(Date)
    vigencia_hasta = Column(Date)
    notas = Column(Text)
