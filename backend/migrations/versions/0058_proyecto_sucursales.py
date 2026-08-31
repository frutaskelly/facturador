"""Sucursales ASIGNADAS a un proyecto (una o más), pedido del dueño 31-ago-2026.

Un proyecto entrega en plazas concretas: «HOSPITALES E IMSS BIENESTAR» surte a
unas sucursales del cliente y no a otras. Hasta hoy esa relación no existía y
cualquier sucursal parecía participar en cualquier proyecto. Esta tabla registra
el alcance: si el proyecto tiene dueño (`cliente_id`), sus sucursales deben ser
de ese cliente; un proyecto del grupo (sin dueño) puede abarcar sucursales de
varios clientes.

Revision ID: 0058_proyecto_sucursales
Revises: 0057_export_rastro_portal_rbac
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0058_proyecto_sucursales"
down_revision: Union[str, None] = "0057_export_rastro_portal_rbac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proyecto_sucursales",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("proyecto_id", UUID(as_uuid=True),
                  sa.ForeignKey("proyectos.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("sucursal_id", UUID(as_uuid=True),
                  sa.ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("proyecto_id", "sucursal_id", name="uq_proyecto_sucursal"),
    )
    # RLS y grants con la MISMA plantilla que el resto de tablas por tenant.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON proyecto_sucursales TO app_user")
    op.execute("ALTER TABLE proyecto_sucursales ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON proyecto_sucursales
            USING (tenant_id = public.current_tenant_id())
        """
    )


def downgrade() -> None:
    op.drop_table("proyecto_sucursales")
