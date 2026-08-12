"""Cobranza F2 — Recibos de Pago (REP, CFDI tipo P) + su detalle por factura.

Un `recibo_pago` = un pago recibido que se timbra como Complemento de Pago 2.0
(uno a la vez, como SAE). Referencia una o varias facturas PPD vía
`recibo_pago_facturas` (docto relacionado: parcialidad + saldos). La bitácora
`timbrado_intentos` se extiende para cubrir también los REP (reconciliación
anti-doble-timbrado compartida con facturas).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038_recibos_pago_rep"
down_revision: Union[str, None] = "0037_factura_saldo_insoluto"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recibos_pago",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cliente_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clientes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("serie", sa.String(10), nullable=False, server_default="P"),
        sa.Column("folio", sa.Integer, nullable=False),
        sa.Column("fecha_pago", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forma_pago", sa.String(5), nullable=False, server_default="03"),  # SAT
        sa.Column("monto", sa.Numeric(18, 4), nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False, server_default="MXN"),
        sa.Column("num_operacion", sa.String(100)),
        sa.Column("banco", sa.String(100)),
        # BORRADOR | TIMBRADO | CANCELADO
        sa.Column("estado", sa.String(10), nullable=False, server_default="BORRADOR"),
        sa.Column("uuid", sa.String(36)),
        sa.Column("facturama_id", sa.String(40)),
        sa.Column("xml", sa.Text()),
        sa.Column("fecha_timbrado", sa.DateTime(timezone=True)),
        sa.Column("fecha_cancelacion", sa.DateTime(timezone=True)),
        sa.Column("motivo_cancelacion", sa.String(2)),
        sa.Column("notas", sa.Text()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "serie", "folio", name="uq_recibo_tenant_serie_folio"),
    )
    op.create_index("ix_recibos_pago_tenant", "recibos_pago", ["tenant_id"])
    op.create_index("ix_recibos_pago_cliente", "recibos_pago", ["cliente_id"])

    op.create_table(
        "recibo_pago_facturas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recibo_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("recibos_pago.id", ondelete="CASCADE"), nullable=False),
        sa.Column("factura_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("facturas.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("importe_pagado", sa.Numeric(18, 4), nullable=False),
        sa.Column("num_parcialidad", sa.Integer, nullable=False, server_default="1"),
        sa.Column("saldo_anterior", sa.Numeric(18, 4), nullable=False),
        sa.Column("saldo_insoluto", sa.Numeric(18, 4), nullable=False),
        sa.Column("moneda_dr", sa.String(3), nullable=False, server_default="MXN"),
    )
    op.create_index("ix_recibo_facturas_tenant", "recibo_pago_facturas", ["tenant_id"])
    op.create_index("ix_recibo_facturas_recibo", "recibo_pago_facturas", ["recibo_id"])
    op.create_index("ix_recibo_facturas_factura", "recibo_pago_facturas", ["factura_id"])

    for tabla in ("recibos_pago", "recibo_pago_facturas"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tabla} TO app_user")
        op.execute(f"ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {tabla} "
            "USING (tenant_id = public.current_tenant_id())"
        )

    # Bitácora compartida: un intento es de una factura O de un recibo (REP).
    op.alter_column("timbrado_intentos", "factura_id", nullable=True)
    op.add_column(
        "timbrado_intentos",
        sa.Column("recibo_pago_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("recibos_pago.id", ondelete="CASCADE")),
    )
    op.create_index("ix_timbrado_intentos_recibo", "timbrado_intentos", ["recibo_pago_id"])


def downgrade() -> None:
    op.drop_index("ix_timbrado_intentos_recibo", table_name="timbrado_intentos")
    op.drop_column("timbrado_intentos", "recibo_pago_id")
    op.alter_column("timbrado_intentos", "factura_id", nullable=False)
    for tabla in ("recibo_pago_facturas", "recibos_pago"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tabla}")
        op.execute(f"ALTER TABLE {tabla} DISABLE ROW LEVEL SECURITY")
    op.drop_table("recibo_pago_facturas")
    op.drop_table("recibos_pago")
