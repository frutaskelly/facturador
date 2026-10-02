"""La lista de precios puede llevar el SKU de sus clientes

Revision ID: 0100_lista_sku_cliente
Revises: 0099_titulo_tabla_cobranza
Create Date: 2026-10-02

Dueño (2-oct-2026): Balles y Jubran necesitan SU SKU en el XML de la factura y
no había dónde ponerlo a mano ni en masa. Se captura desde la lista de precios
(los dos comparten la suya): con `lleva_sku_cliente` encendido la lista enseña
la columna «SKU del cliente» y el Excel de ida y vuelta la trae.

El SKU NO vive en la lista: se guarda en el catálogo de cada cliente asignado a
ella (`producto_clientes.codigo_cliente`, en su unidad), que es lo que leen el
XML del Facturador y el masivo de SAE. La lista sólo es la puerta para editarlo.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0100_lista_sku_cliente"
down_revision: Union[str, None] = "0099_titulo_tabla_cobranza"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("listas_precios", sa.Column("lleva_sku_cliente", sa.Boolean(), nullable=False,
                                              server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("listas_precios", "lleva_sku_cliente")
