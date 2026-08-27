"""Catálogo por cliente — cómo llama CADA cliente a un producto del catálogo.

Un solo producto interno ("JITOMATE SALADETT") puede salir en el CFDI de un
cliente como "JITOMATE ROMA" con su código "JIT-SAD-001" y en el de otro con el
nombre/SKU internos. Esta tabla guarda ese mapeo (cliente, producto) →
{codigo_cliente → NoIdentificacion, nombre_cliente → Descripcion}; el builder
del CFDI (services/cfdi.py) lo aplica al timbrar. Así NUNCA se duplica el
producto por cliente. Único por (tenant, cliente, producto).
"""
from sqlalchemy import Column, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ..core.db import Base
from .base import TimestampMixin, tenant_fk, uuid_pk


class ProductoCliente(Base, TimestampMixin):
    __tablename__ = "producto_clientes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "cliente_id", "producto_id", name="uq_producto_cliente"
        ),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    cliente_id = Column(
        UUID(as_uuid=True),
        ForeignKey("clientes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    producto_id = Column(
        UUID(as_uuid=True),
        ForeignKey("productos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Código del cliente (su "NoIdentificacion"), p. ej. "JIT-SAD-001".
    codigo_cliente = Column(String(50))
    # Nombre con el que el cliente conoce el producto (Descripcion del CFDI).
    nombre_cliente = Column(String(254))
    # Unidad con la que ESE cliente compra ("Cilantro por manojo" → MANOJO):
    # debe existir en las presentaciones del producto. Vacía = la default.
    presentacion = Column(String(20))

    producto = relationship("Producto")
