"""POS Fase 2 — corte de caja con fondo inicial + arqueo.

Un `pos_corte` = un turno de caja de un usuario: se ABRE declarando el fondo
inicial y se CIERRA declarando el efectivo contado; el descuadre = contado −
(fondo + ventas en efectivo del turno). Los `pagos` del turno se enlazan por
`corte_id` para el resumen por forma de pago.

`limite_credito` / `saldo_actual` de clientes YA existen (modelo dormido); esta
migración solo agrega la caja y el vínculo pago↔corte.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_pos_corte_caja"
down_revision: Union[str, None] = "0035_pos_etapa_ancha"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pos_cortes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("estado", sa.String(10), nullable=False, server_default="ABIERTO"),  # ABIERTO|CERRADO
        sa.Column("fondo_inicial", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("efectivo_contado", sa.Numeric(18, 4)),   # al cerrar
        sa.Column("abierto_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("cerrado_at", sa.DateTime(timezone=True)),
        sa.Column("notas", sa.Text()),
    )
    op.create_index("ix_pos_cortes_tenant", "pos_cortes", ["tenant_id"])
    op.create_index("ix_pos_cortes_user", "pos_cortes", ["user_id"])
    # A lo más UN corte ABIERTO por usuario a la vez.
    op.create_index(
        "uq_pos_corte_abierto", "pos_cortes", ["tenant_id", "user_id"],
        unique=True, postgresql_where=sa.text("estado = 'ABIERTO'"),
    )
    op.add_column("pagos", sa.Column("corte_id", postgresql.UUID(as_uuid=True),
                                     sa.ForeignKey("pos_cortes.id", ondelete="SET NULL")))
    op.create_index("ix_pagos_corte", "pagos", ["corte_id"])

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON pos_cortes TO app_user")
    op.execute("ALTER TABLE pos_cortes ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON pos_cortes "
        "USING (tenant_id = public.current_tenant_id())"
    )


def downgrade() -> None:
    op.drop_index("ix_pagos_corte", table_name="pagos")
    op.drop_column("pagos", "corte_id")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON pos_cortes")
    op.execute("ALTER TABLE pos_cortes DISABLE ROW LEVEL SECURITY")
    op.drop_table("pos_cortes")
