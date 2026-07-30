"""Devoluciones desde remisión (decisión 2026-07-29: ajustan la remisión a lo neto).

Tablas de rastro: qué se devolvió de qué remisión. El efecto (líneas de la
remisión reducidas, inventario a disponible, ENTRADA_DEVOLUCION) lo aplica el
endpoint; aquí solo el registro.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_devoluciones"
down_revision: Union[str, None] = "0032_remision_impuestos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "devoluciones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("remision_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("remisiones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("motivo", sa.Text()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_devoluciones_tenant", "devoluciones", ["tenant_id"])
    op.create_index("ix_devoluciones_remision", "devoluciones", ["remision_id"])
    op.create_table(
        "lineas_devolucion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("devolucion_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devoluciones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("linea_remision_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("lineas_remision.id", ondelete="SET NULL")),
        sa.Column("producto_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("productos.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("presentacion", sa.String(20), nullable=False, server_default="KILO"),
        sa.Column("cantidad", sa.Numeric(18, 4), nullable=False),
        sa.Column("cantidad_base", sa.Numeric(18, 4), nullable=False),
    )
    op.create_index("ix_lineas_devolucion_tenant", "lineas_devolucion", ["tenant_id"])
    op.create_index("ix_lineas_devolucion_devolucion", "lineas_devolucion", ["devolucion_id"])
    for tabla in ("devoluciones", "lineas_devolucion"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tabla} TO app_user")
        op.execute(f"ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {tabla} "
            "USING (tenant_id = public.current_tenant_id())"
        )


def downgrade() -> None:
    for tabla in ("lineas_devolucion", "devoluciones"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tabla}")
        op.execute(f"ALTER TABLE {tabla} DISABLE ROW LEVEL SECURITY")
    op.drop_table("lineas_devolucion")
    op.drop_table("devoluciones")
