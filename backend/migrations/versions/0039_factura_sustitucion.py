"""factura: sustitución CFDI (nueva → vieja) para CfdiRelacionados tipo 04

Revision ID: 0039_factura_sustitucion
Revises: 0038_recibos_pago_rep
Create Date: 2026-08-13

Agrega facturas.sustituye_a_factura_id: la factura NUEVA apunta a la VIEJA a la
que sustituye. Al timbrar la nueva se emite el nodo Relations TipoRelacion "04"
(Sustitución de los CFDI previos) con el UUID de la vieja. El sentido inverso
(vieja → nueva) ya existe en facturas.uuid_sustitucion, que se llena al cancelar
la vieja con motivo "01".
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0039_factura_sustitucion"
down_revision: Union[str, None] = "0038_recibos_pago_rep"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "facturas",
        sa.Column(
            "sustituye_a_factura_id",
            UUID(as_uuid=True),
            sa.ForeignKey("facturas.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_facturas_sustituye_a_factura_id", "facturas", ["sustituye_a_factura_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_facturas_sustituye_a_factura_id", table_name="facturas")
    op.drop_column("facturas", "sustituye_a_factura_id")
