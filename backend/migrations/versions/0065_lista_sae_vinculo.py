"""Vínculo lista de precios ↔ lista de precios de SAE.

El botón «Sincronizar SAE» de /listas-precios refresca los precios desde
Aspel, pero el conector solo puede escribir en listas que declaren de dónde
vienen: `sae_empresa` (la empresa de Aspel: "02" Pachuca, "03" Tabasco) y
`sae_lista` (el CVE_PRECIO de PRECIO_X_PROD). Ambas en NULL = lista manual,
el espejo no la toca.

Revision ID: 0065_lista_sae_vinculo
Revises: 0064_espejo_syncs
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0065_lista_sae_vinculo"
down_revision: Union[str, None] = "0064_espejo_syncs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("listas_precios", sa.Column("sae_empresa", sa.String(4)))
    op.add_column("listas_precios", sa.Column("sae_lista", sa.SmallInteger()))


def downgrade() -> None:
    op.drop_column("listas_precios", "sae_lista")
    op.drop_column("listas_precios", "sae_empresa")
