"""«Sincronizar SAE» desde la UI + la fecha de la última actualización.

El backend no ve SAE: quien lo consulta es el conector (sqlcmd desde la Mac) y
deposita en el espejo. El botón de /facturas y /remisiones funciona entonces
por solicitud: deja una fila PENDIENTE aquí, el conector la reclama (EN_CURSO),
corre el espejo y reporta OK/ERROR con su resultado. Las pasadas automáticas de
cada 30 min también reportan al terminar — de esa fila sale «SAE actualizado:
<fecha>» aunque nadie presione nada.

Revision ID: 0064_espejo_syncs
Revises: 0063_export_pedido_rastro
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064_espejo_syncs"
down_revision: Union[str, None] = "0063_export_pedido_rastro"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "espejo_syncs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        # PENDIENTE → EN_CURSO (el conector la reclamó) → OK | ERROR
        sa.Column("estado", sa.String(10), nullable=False, server_default="PENDIENTE"),
        # MANUAL = botón · AUTOMATICA = la pasada del timer del conector
        sa.Column("origen", sa.String(12), nullable=False, server_default="MANUAL"),
        sa.Column("dias", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("solicitada_por", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("solicitada_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("iniciada_at", sa.DateTime(timezone=True)),
        sa.Column("terminada_at", sa.DateTime(timezone=True)),
        sa.Column("resultado", postgresql.JSONB()),
    )
    op.create_index("ix_espejo_syncs_tenant", "espejo_syncs", ["tenant_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON espejo_syncs TO app_user")
    op.execute("ALTER TABLE espejo_syncs ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON espejo_syncs "
        "USING (tenant_id = public.current_tenant_id())"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON espejo_syncs")
    op.execute("ALTER TABLE espejo_syncs DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_espejo_syncs_tenant", table_name="espejo_syncs")
    op.drop_table("espejo_syncs")
