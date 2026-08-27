"""La remisión guarda el folio de su factura en SAE y el estado RESERVADO

Revision ID: 0051_remision_factura_sae
Revises: 0050_proyectos_y_asignaciones
Create Date: 2026-08-27

El traspaso desde SAE necesita la relación en los dos sentidos: qué factura de
SAE ampara cada remisión. Se guarda el folio tal cual sale de SAE ("ZHGO 233")
en `factura_sae` —texto libre, es un folio de OTRO sistema— y la remisión que
lo trae deja de ser un borrador cualquiera: pasa a RESERVADO, o sea mercancía
ya comprometida con una factura que vive fuera del facturador.

RESERVADO no mueve inventario: la salida sigue siendo el confirmar (decisión
2026-07-29: confirmar = el camión salió).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0051_remision_factura_sae"
down_revision: Union[str, None] = "0050_proyectos_y_asignaciones"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE no puede correr dentro de la transacción de la
    # migración si el valor se usa después; va en un bloque autocommit.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE remision_estado ADD VALUE IF NOT EXISTS 'RESERVADO'")
    op.add_column("remisiones", sa.Column("factura_sae", sa.String(30)))
    # El cruce contra el archivo de facturas de SAE busca por este folio.
    op.create_index(
        "ix_remisiones_factura_sae",
        "remisiones",
        ["tenant_id", "factura_sae"],
        postgresql_where=sa.text("factura_sae IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_remisiones_factura_sae", table_name="remisiones")
    op.drop_column("remisiones", "factura_sae")
    # Postgres no soporta quitar un valor de un ENUM; el estado queda.
