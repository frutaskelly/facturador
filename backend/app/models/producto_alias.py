"""Alias aprendidos de producto — el cruce de productos.

Cada fila mapea un texto que escribió/pegó el usuario ("zanahorias", "Chile
Cuaresmeño") al producto real del catálogo. Se crea cuando el usuario confirma
una sugerencia; a partir de ahí el resolutor lo encuentra al instante y no
vuelve a preguntar.

Alcance (migración 0053): `cliente_id`/`sucursal_id` en NULL = alias GLOBAL del
tenant, el caso normal — así el vocabulario le sirve a todos los clientes. Solo
cuando el mismo texto significa cosas distintas por cliente (el "LIMON" de
Pachuca no es el de Villahermosa) se guarda con alcance, y el resolutor
prefiere el más específico: cliente+sucursal > cliente > global. Único por
(tenant, cliente, sucursal, alias_normalizado) vía índice funcional con
COALESCE (NULL cuenta como valor).
"""
from sqlalchemy import Column, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID

from ..core.db import Base
from .base import tenant_fk, uuid_pk


class ProductoAlias(Base):
    __tablename__ = "producto_alias"
    # La unicidad vive en el índice funcional `uq_alias_tenant_alcance_norm`
    # (migración 0053): UNIQUE (tenant, COALESCE(cliente), COALESCE(sucursal),
    # alias_normalizado). No se declara aquí porque SQLAlchemy no expresa
    # COALESCE en UniqueConstraint.

    id = uuid_pk()
    tenant_id = tenant_fk()
    producto_id = Column(UUID(as_uuid=True), ForeignKey("productos.id", ondelete="CASCADE"), nullable=False, index=True)
    # NULL = global (el caso normal). Con valor = vocabulario privado del
    # cliente (y de su sucursal, para clientes con vocabularios regionales).
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"), nullable=True)
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=True)
    alias = Column(String(254), nullable=False)
    alias_normalizado = Column(String(254), nullable=False)
    origen = Column(String(12), nullable=False, server_default="MANUAL")  # MANUAL | IA | IMPORT
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
