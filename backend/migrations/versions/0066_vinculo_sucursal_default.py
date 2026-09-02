"""Sucursal por defecto del cliente (en el vínculo cliente×plaza).

Una remisión sin sucursal no puede ganar ninguna lista ni override anclados a
plaza (regla del resolutor), así que capturar sin elegirla deja las líneas sin
precio. El flag vive en el VÍNCULO —no en el cliente— porque la plaza ya no es
del cliente (0060): marcar `es_default` hace que los formularios la preseleccionen
cuando el cliente tiene varias. A lo más una por cliente (índice parcial).

Revision ID: 0066_vinculo_sucursal_default
Revises: 0065_lista_sae_vinculo
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0066_vinculo_sucursal_default"
down_revision: Union[str, None] = "0065_lista_sae_vinculo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cliente_sucursales",
        sa.Column("es_default", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index(
        "uq_cliente_sucursal_default",
        "cliente_sucursales",
        ["cliente_id"],
        unique=True,
        postgresql_where=sa.text("es_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_cliente_sucursal_default", table_name="cliente_sucursales")
    op.drop_column("cliente_sucursales", "es_default")
