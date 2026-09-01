"""Rastro del export de PEDIDOS (folio propuesto por empresa SAE)

Revision ID: 0063_export_pedido_rastro
Revises: 0062_remision_por_revisar
Create Date: 2026-09-01

El masivo de PEDIDOS llevaba la OC del cliente en la columna FOLIO
("CE-34CER-MAR"). Está mal: el FOLIO de un pedido es el consecutivo del SAE
(REGLAS_PEDIDOS.md Regla 1-2 y KnowHow_Massivos_SAE.md §2) — una sola serie
'STAND.' (TIP_DOC='P') numerada POR EMPRESA. Ahora el operador confirma el
folio inicial contra SAE, igual que en facturas, y estas dos columnas guardan
lo que se propuso para que el siguiente lote no repita el rango.

Van aparte de `export_sae_at`/`export_sae_folio` (facturas) porque la misma
remisión sale primero como pedido y luego como factura: una sola columna
borraría el aviso de doble export de la otra.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0063_export_pedido_rastro"
down_revision: Union[str, None] = "0062_remision_por_revisar"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("remisiones", sa.Column("export_pedido_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("remisiones", sa.Column("export_pedido_folio", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("remisiones", "export_pedido_folio")
    op.drop_column("remisiones", "export_pedido_at")
