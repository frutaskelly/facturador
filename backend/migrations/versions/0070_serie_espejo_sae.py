"""El candado del espejo baja del cliente a la serie.

`clientes.espejo_sae` bloqueaba facturar nativo al cliente COMPLETO, pero el
corte del SAE es por plaza: EHMO Pachuca pasa a timbrar nativo (FEHMOHOS)
mientras EHMO Tabasco sigue espejado (ZEHMOVH) — el mismo cliente en ambos.
La serie es quien sabe de qué lado del corte está cada venta: se marca
`series.espejo_sae` y el candado de facturas nativas consulta la serie
resuelta, no solo al cliente.

Backfill: una serie de FACTURA es espejo si ya refleja facturas del SAE
(facturas con origen ESPEJO_SAE de su mismo tenant y código). Así ZHGO,
ZEHMOHOS, ZEHMOVH y ZMAFAN quedan marcadas y RIO (nativa) queda apagada,
sin listas fijas de códigos.

Revision ID: 0070_serie_espejo_sae
Revises: 0068_indices_fk_calientes
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0070_serie_espejo_sae"
down_revision: Union[str, None] = "0068_indices_fk_calientes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "series",
        sa.Column("espejo_sae", sa.Boolean(), nullable=False, server_default="false"),
    )
    # El espejo normaliza la serie al depositar (strip().upper()); series.codigo
    # se guarda tal cual, así que el cruce del backfill normaliza del lado de la
    # serie o un código 'Zhgo ' dejaría su serie sin marcar (candado abierto).
    op.execute(
        """
        UPDATE series s
           SET espejo_sae = true
         WHERE s.tipo_documento = 'FACTURA'
           AND EXISTS (
               SELECT 1 FROM facturas f
                WHERE f.tenant_id = s.tenant_id
                  AND f.serie = upper(btrim(s.codigo))
                  AND f.origen = 'ESPEJO_SAE'
           )
        """
    )


def downgrade() -> None:
    op.drop_column("series", "espejo_sae")
