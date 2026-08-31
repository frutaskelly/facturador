"""Proyectos — la negociación con nombre propio.

«HOSPITALES E IMSS BIENESTAR», «CERESOS Y SEGURIDAD PÚBLICA». Hasta ahora esto
sólo existía como texto suelto dentro de la orden de compra; ahora es una
entidad, porque es a este nivel al que el negocio pacta precios: el mismo
cliente, en la misma sucursal, cobra distinto según el proyecto al que entrega.

`cliente_id` es opcional a propósito: casi siempre el proyecto es de un cliente,
pero un mismo programa de gobierno puede comprarse a través de varias razones
sociales, y forzar el dueño obligaría a duplicarlo.
"""
from sqlalchemy import Boolean, Column, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ..core.db import Base
from .base import SoftDeleteMixin, TimestampMixin, tenant_fk, uuid_pk


class ProyectoSucursal(Base):
    """Sucursal ASIGNADA al proyecto (migración 0058). El alcance del proyecto:
    a qué plazas entrega. Si el proyecto tiene dueño, sus sucursales son de ese
    cliente; uno del grupo puede abarcar sucursales de varios clientes."""
    __tablename__ = "proyecto_sucursales"
    __table_args__ = (
        UniqueConstraint("proyecto_id", "sucursal_id", name="uq_proyecto_sucursal"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    proyecto_id = Column(UUID(as_uuid=True), ForeignKey("proyectos.id", ondelete="CASCADE"), nullable=False, index=True)
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False)


class Proyecto(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "proyectos"
    __table_args__ = (
        UniqueConstraint("tenant_id", "codigo", name="uq_proyecto_tenant_codigo"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    # Se autogenera del nombre (convención de la casa: el usuario no lo teclea).
    codigo = Column(String(20), nullable=False)
    nombre = Column(String(254), nullable=False)
    cliente_id = Column(
        UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="CASCADE"), index=True
    )
    activo = Column(Boolean, nullable=False, server_default="true")
    notas = Column(Text)

    cliente = relationship("Cliente")

    @property
    def cliente_nombre(self):
        return self.cliente.legal_name if self.cliente else None
