"""Una remisión puede llegar SIN REVISAR desde la bandeja de órdenes.

Hasta hoy una orden solo se volvía remisión cuando alguien la había revisado
partida por partida: o a mano en la pantalla de la orden, o de un clic pero
únicamente si TODO había cruzado por vía determinista. Cuando algo no cuadraba
—una unidad rara, un precio en conflicto, una partida que no cruzó— la orden se
quedaba en la bandeja y el trabajo se acumulaba ahí.

El pedido del dueño (1-sep-2026) es poder pasarla de todos modos y revisarla
después, ya del lado de Remisiones, que es donde se trabaja el documento. Para
eso hacen falta dos cosas que la remisión no sabía decir:

`revision_pendiente` — la remisión existe y tiene folio, pero nadie la ha mirado
todavía. Mientras esté encendida no se confirma, no se factura y no sale en el
export a SAE: sin ese freno, «sin revisar» significaría que una unidad adivinada
llega al inventario y al SAT sin que nadie la haya visto.

`partidas_por_cruzar` — las partidas de la orden que NO encontraron producto en
el catálogo, guardadas tal como venían (texto, cantidad, unidad, clave). Una
línea de remisión no existe sin producto (`lineas_remision.producto_id` es NOT
NULL, y de él cuelgan el precio, el impuesto y el inventario), así que estas
viajan aparte en vez de perderse: la pantalla de la remisión las enseña para
que alguien las cruce a mano.

Revision ID: 0062_remision_por_revisar
Revises: 0061_fusion_plazas
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0062_remision_por_revisar"
down_revision: Union[str, None] = "0061_fusion_plazas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Lo que ya existe fue revisado por definición: nació del flujo que exigía
    # revisión. Por eso el default es `false` y no hay backfill que hacer.
    op.add_column(
        "remisiones",
        sa.Column(
            "revision_pendiente",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "remisiones",
        sa.Column(
            "partidas_por_cruzar",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # La lista de remisiones filtra por «por revisar» y esa bandeja se consulta
    # todo el día; el índice parcial solo indexa las que lo están, que son unas
    # pocas frente al histórico.
    op.create_index(
        "ix_remisiones_revision_pendiente",
        "remisiones",
        ["tenant_id"],
        postgresql_where=sa.text("revision_pendiente"),
    )


def downgrade() -> None:
    op.drop_index("ix_remisiones_revision_pendiente", table_name="remisiones")
    op.drop_column("remisiones", "partidas_por_cruzar")
    op.drop_column("remisiones", "revision_pendiente")
