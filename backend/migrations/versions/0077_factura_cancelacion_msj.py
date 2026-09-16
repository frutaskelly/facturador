"""La cancelación PEDIDA al SAT, que no es lo mismo que cancelada.

SAE marca `STATUS='C'` sólo cuando el SAT ya aceptó. Mientras tanto la factura
sigue "viva" ahí, pero su cancelación ya está enviada y el cliente no la va a
pagar: la verdad vive en `CFDIxx.MSJ_CANC` ("Cancelación enviada al SAT",
"En espera de aprobación", "No Cancelable"). Al medirlo el 15-sep-2026 había
~$1.5M en ese limbo dentro de las series espejeadas, y el estado de cuenta los
cobraba como si nada.

Guardamos el mensaje tal cual lo da SAE —no un booleano— porque "No Cancelable"
significa lo contrario que los otros dos y conviene poder distinguirlos sin
volver a SAE.

Revision ID: 0077_factura_cancelacion_msj
Revises: 0075_indices_listados

OJO: en paralelo vive `0076_claves_sae` (rama claude/validar-claves-sae), que
también cuelga de la 0075. El segundo en fusionar tiene que repuntar su
`down_revision` al otro, o alembic queda con dos cabezas.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0077_factura_cancelacion_msj"
down_revision: Union[str, None] = "0075_indices_listados"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "facturas",
        sa.Column("cancelacion_msj", sa.String(length=120), nullable=True),
    )
    # El estado de cuenta filtra por esto en cada consulta: sin índice, cada
    # corte de un cliente con cientos de facturas lo paga en secuencial.
    op.create_index(
        "ix_facturas_cancelacion_msj",
        "facturas",
        ["tenant_id", "cliente_id"],
        postgresql_where=sa.text("cancelacion_msj IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_facturas_cancelacion_msj", table_name="facturas")
    op.drop_column("facturas", "cancelacion_msj")
