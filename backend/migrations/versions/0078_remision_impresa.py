"""Una remisión impresa ya no la reescribe una sincronización.

El 15 y el 16 de septiembre de 2026 el vigía del bot (Master de Sheets ->
Facturador, cada hora) reescribió remisiones de la semana 38 que ya se habían
impreso, entregado y firmado. Les devolvió las cantidades del PEDIDO encima de
los pesos de BÁSCULA que bodega había capturado, y volvió a meter partidas que
el cliente no recibió. Nueve de once dejaron de cuadrar contra el papel firmado:
ocho cobraban de menos y RZEHMOVH177 cobraba $408.36 de más que la remisión que
el cliente tiene sellada.

`impresa_at` es la marca de "ya hay un papel en la calle". La estampa el
endpoint del PDF la primera vez que sale, y con ella puesta el PATCH rechaza
cualquier cambio que venga de una CONEXIÓN (el bot) en vez de una persona. Las
personas siguen pudiendo corregir: quien decide qué hacer con una remisión ya
firmada es el equipo, no una sincronización de cada hora.

Se rellena hacia atrás con `updated_at` para las remisiones de EHMO
Villahermosa de la semana 38, que son las que ya están impresas y firmadas
(14-sep-2026) y las que el vigía volvería a pisar en su siguiente pasada.

Revision ID: 0078_remision_impresa
Revises: 0076_claves_sae

OJO: hay otras ramas abiertas que también estrenan 0078 (PR #161, bitácora de
importación de productos). El segundo en fusionar repunta su `down_revision` al
otro, o alembic queda con dos cabezas.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0078_remision_impresa"
down_revision: Union[str, None] = "0076_claves_sae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Las once de la semana 38 que ya se imprimieron y firmaron el 14-sep-2026.
# Se listan a mano y no por fecha: "impresa" es un hecho del mundo real, no algo
# que se pueda deducir de la base, y marcar de más congelaría remisiones que
# nadie ha sacado en papel.
FOLIOS_SEM38 = (
    "RZEHMOVH168", "RZEHMOVH169", "RZEHMOVH172", "RZEHMOVH173",
    "RZEHMOVH174", "RZEHMOVH175", "RZEHMOVH176", "RZEHMOVH177",
    "RZEHMOVH178", "RZEHMOVH179", "RZEHMOVH180",
)


def upgrade() -> None:
    op.add_column(
        "remisiones",
        sa.Column("impresa_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "update remisiones set impresa_at = coalesce(impresa_at, updated_at)"
            " where folio_interno = any(:folios) and deleted_at is null"
        ).bindparams(folios=list(FOLIOS_SEM38))
    )


def downgrade() -> None:
    op.drop_column("remisiones", "impresa_at")
