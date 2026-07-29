"""Impuestos en remisiones (decisión 2026-07-29: la remisión muestra IVA/IEPS).

Columnas informativas por línea; los totales del encabezado (remisiones.iva/
ieps, que ya existían en 0) se llenan al crear/editar con el mismo cerebro
fiscal de las facturas. El backfill del histórico NO va aquí: lo hace
`scripts/backfill_remisiones_impuestos.py` reutilizando services/fiscal.py
(replicar la lógica fiscal en SQL crearía un segundo cerebro).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_remision_impuestos"
down_revision: Union[str, None] = "0031_timbrado_y_reservas"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "lineas_remision",
        sa.Column("iva_importe", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "lineas_remision",
        sa.Column("ieps_importe", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("lineas_remision", "ieps_importe")
    op.drop_column("lineas_remision", "iva_importe")
