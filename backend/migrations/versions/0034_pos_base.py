"""POS Fase 0 — pipeline configurable sobre remisiones + tabla de pagos.

- `remisiones.pos_etapa`: estación donde el pedido ESPERA (pedido/caja/almacen/
  salida/completado); NULL = remisión normal, fuera del POS. El pipeline activo
  vive en `tenants.config.pos` (qué etapas prende cada cliente).
- `remisiones.pos_asignaciones`: {etapa: {user_id, at}} — quién completó qué.
- `pagos`: cobros del POS (y futuros abonos) — contado por forma de pago y/o
  crédito; alimenta los acumuladores de clientes (saldo/última venta) en Fase 2.

RBAC: los permisos del POS (menu:pos.*, pedido:capturar/cobrar/surtir/entregar)
y los roles TOMADOR/CAJERO/BODEGUERO/REPARTIDOR YA existen desde 0003_seed_iam.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034_pos_base"
down_revision: Union[str, None] = "0033_devoluciones"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("remisiones", sa.Column("pos_etapa", sa.String(12)))
    op.add_column(
        "remisiones",
        sa.Column("pos_asignaciones", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_remisiones_pos_etapa", "remisiones", ["pos_etapa"])

    op.create_table(
        "pagos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cliente_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clientes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("remision_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("remisiones.id", ondelete="SET NULL")),
        sa.Column("factura_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("facturas.id", ondelete="SET NULL")),
        sa.Column("fecha", sa.Date, nullable=False, server_default=sa.text("CURRENT_DATE")),
        sa.Column("monto", sa.Numeric(18, 4), nullable=False),
        # Forma de pago SAT: 01 efectivo, 03 transferencia, 04 tarjeta, 99 crédito/otros.
        sa.Column("forma_pago", sa.String(5), nullable=False, server_default="01"),
        sa.Column("banco", sa.String(100)),
        sa.Column("referencia", sa.String(100)),
        sa.Column("notas", sa.Text()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_pagos_tenant", "pagos", ["tenant_id"])
    op.create_index("ix_pagos_cliente", "pagos", ["cliente_id"])
    op.create_index("ix_pagos_remision", "pagos", ["remision_id"])
    op.create_index("ix_pagos_fecha", "pagos", ["fecha"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON pagos TO app_user")
    op.execute("ALTER TABLE pagos ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON pagos "
        "USING (tenant_id = public.current_tenant_id())"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON pagos")
    op.execute("ALTER TABLE pagos DISABLE ROW LEVEL SECURITY")
    op.drop_table("pagos")
    op.drop_index("ix_remisiones_pos_etapa", table_name="remisiones")
    op.drop_column("remisiones", "pos_asignaciones")
    op.drop_column("remisiones", "pos_etapa")
