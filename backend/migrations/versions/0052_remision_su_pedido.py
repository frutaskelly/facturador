"""La remisión guarda "su pedido": la orden de compra del cliente

Revision ID: 0052_remision_su_pedido
Revises: 0051_remision_factura_sae
Create Date: 2026-08-27

La CLAVE del archivo del cliente (0000024478) es SU número de orden de compra,
y es la llave con la que él reconoce el documento. Vivía enterrada en las notas;
ahora es columna propia (`su_pedido`) para buscarla, ordenarla y cruzarla contra
sus archivos.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0052_remision_su_pedido"
down_revision: Union[str, None] = "0051_remision_factura_sae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("remisiones", sa.Column("su_pedido", sa.String(30)))
    op.create_index(
        "ix_remisiones_su_pedido",
        "remisiones",
        ["tenant_id", "su_pedido"],
        postgresql_where=sa.text("su_pedido IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_remisiones_su_pedido", table_name="remisiones")
    op.drop_column("remisiones", "su_pedido")
