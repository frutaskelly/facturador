"""Cobranza F1 — `facturas.saldo_insoluto` (base del estado de cuenta / REP).

Para una factura PPD timbrada, el saldo insoluto arranca = total y baja con cada
abono (REP, F2). PUE / no timbradas / no PPD → 0 (no generan cuenta por cobrar).
Backfill de las facturas ya timbradas según su método de pago.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037_factura_saldo_insoluto"
down_revision: Union[str, None] = "0036_pos_corte_caja"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "facturas",
        sa.Column("saldo_insoluto", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    # Backfill: PPD timbradas (no canceladas) deben cobrarse → saldo = total.
    op.execute(
        """
        UPDATE facturas
           SET saldo_insoluto = total
         WHERE estado = 'TIMBRADA' AND metodo_pago = 'PPD' AND deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("facturas", "saldo_insoluto")
