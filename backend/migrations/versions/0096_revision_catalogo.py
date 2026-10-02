"""Revisión del catálogo: lo que se decidió sobre cada grupo de productos

Revision ID: 0096_revision_catalogo
Revises: 0095_almacen_del_proyecto
Create Date: 2026-10-02

Dueño (2-oct-2026): la hoja de Excel de grupos se quedaba vieja en cuanto se
unía algo. La pantalla /productos/revision calcula los grupos en vivo y esta
tabla guarda sólo la decisión de cada uno. Sin permisos nuevos: ver es
`menu:productos` y decidir es `producto:gestionar`, lo mismo que reapuntar un
sinónimo (los dos afectan a todo el catálogo).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0096_revision_catalogo"
down_revision: Union[str, None] = "0095_almacen_del_proyecto"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "revision_catalogo",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("grupo", sa.String(300), nullable=False),
        sa.Column("estado", sa.String(10), nullable=False),
        sa.Column("firma", sa.Text(), nullable=False),
        sa.Column("propuesta", postgresql.JSONB()),
        sa.Column("nota", sa.Text()),
        sa.Column("decidido_por", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decidido_por_email", sa.String(160)),
        sa.Column("decidido_at", sa.DateTime(timezone=True)),
        sa.Column("aplicado_at", sa.DateTime(timezone=True)),
        sa.Column("aplicado_resumen", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "grupo", name="uq_revision_catalogo_grupo"),
    )
    op.create_index("ix_revision_catalogo_tenant_id", "revision_catalogo", ["tenant_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON revision_catalogo TO app_user")
    op.execute("ALTER TABLE revision_catalogo ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON revision_catalogo "
        "USING (tenant_id = public.current_tenant_id())"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON revision_catalogo")
    op.drop_index("ix_revision_catalogo_tenant_id", table_name="revision_catalogo")
    op.drop_table("revision_catalogo")
