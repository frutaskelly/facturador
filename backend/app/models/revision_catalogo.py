"""Decisiones de la revisión del catálogo — qué grupos de productos se unen.

Los grupos NO se guardan: se calculan en cada consulta con los datos del momento
(`services/revision_catalogo.py`). Aquí sólo vive lo que una persona decidió
sobre un grupo, con la propuesta tal como la vio y la `firma` de quiénes lo
formaban: si el grupo cambia después (alguien dio de alta otro gemelo, otro
producto ganó una unidad) la decisión queda marcada como desactualizada en vez
de aplicarse a ciegas.

Sin fila = PENDIENTE. Unir lo aprobado es un paso aparte y deja APLICADO.
"""
from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..core.db import Base
from .base import TimestampMixin, tenant_fk, uuid_pk

REVISION_ESTADOS = ("APROBADO", "RECHAZADO", "APLICADO")


class RevisionCatalogo(Base, TimestampMixin):
    __tablename__ = "revision_catalogo"
    __table_args__ = (UniqueConstraint("tenant_id", "grupo", name="uq_revision_catalogo_grupo"),)

    id = uuid_pk()
    tenant_id = tenant_fk()
    # «R:ESPINACA» (misma raíz de nombre) o «E:00010006» (empaque vendido por kilo).
    grupo = Column(String(300), nullable=False)
    estado = Column(String(10), nullable=False)
    firma = Column(Text, nullable=False)
    # Lo aprobado, tal cual: quién se queda, nombre, clave por unidad, qué unidades se quitan.
    propuesta = Column(JSONB)
    nota = Column(Text)
    decidido_por = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    decidido_por_email = Column(String(160))
    decidido_at = Column(DateTime(timezone=True))
    aplicado_at = Column(DateTime(timezone=True))
    aplicado_resumen = Column(JSONB)
