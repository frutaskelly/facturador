"""Facturas editables en borrador: su_pedido + presentación por línea.

`facturas.su_pedido` guarda la OC del cliente en la factura DIRECTA (en una
factura desde remisiones la OC vive en cada remisión ligada, pero la directa
no tiene remisión donde anotarla).

`lineas_factura.presentacion` persiste la presentación con la que se capturó
la línea directa: clave_unidad y cantidad_base se derivan de ella al guardar,
y sin persistirla la edición del borrador no puede reconstruir qué eligió el
usuario. Queda NULL en líneas históricas y en facturas desde remisiones.

Revision ID: 0071_factura_su_pedido
Revises: 0070_serie_espejo_sae
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0071_factura_su_pedido"
down_revision: Union[str, None] = "0070_serie_espejo_sae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("facturas", sa.Column("su_pedido", sa.String(length=30), nullable=True))
    op.add_column("lineas_factura", sa.Column("presentacion", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("lineas_factura", "presentacion")
    op.drop_column("facturas", "su_pedido")
