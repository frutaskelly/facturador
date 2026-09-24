"""La fecha de entrega de una OC recibida, como columna consultable.

Primer paso del retiro del Master de EHMO. Ese Master tiene cinco candados
antiduplicado que viven leyendo la hoja; el primero que se mueve es el de
FOLIO REPETIDO, y es el que más urge por una razón incómoda: hoy, cuando el
bot detecta un folio repetido, ABORTA — pero si ese candado desapareciera, la
ingesta del Facturador no duplicaría la orden: la SOBRESCRIBIRÍA. Es el único
de los cinco cuyo hueco no duplica sino que BORRA, en silencio.

Para poder decidirlo hace falta comparar la fecha de entrega de la orden que
llega contra la que ya está guardada, y esa fecha hoy vive dentro del payload
JSONB: se puede leer, pero sin índice, o sea recorriendo la tabla entera en
cada alta.

Dos índices más, que el propio retiro necesita:
  · (tenant_id, folio_externo, fecha_entrega) para el candado.
  · (tenant_id, archivo_nombre) para el contador de sufijos «aparte», que hoy
    también sale de la hoja. Parcial, porque la mayoría de las órdenes no lo
    traen.

Revision ID: 0087_fecha_entrega_oc_recibida
Revises: 0086_espejo_pagos_notas_credito
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0087_fecha_entrega_oc_recibida"
down_revision: Union[str, None] = "0086_espejo_pagos_notas_credito"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("oc_recibidas", sa.Column("fecha_entrega", sa.Date(), nullable=True))
    # El dato ya viajaba en el payload desde el primer día: se rescata en vez de
    # empezar de cero, para que el candado sirva desde la primera alta y no
    # dentro de un mes. Un texto que no sea fecha se deja en NULL en vez de
    # tumbar la migración: el candado trata NULL como «no sé» y no bloquea.
    op.execute(
        """
        UPDATE oc_recibidas
           SET fecha_entrega = CASE
                 WHEN payload->>'fecha_entrega' ~ '^\\d{4}-\\d{2}-\\d{2}$'
                 THEN (payload->>'fecha_entrega')::date
               END
         WHERE payload ? 'fecha_entrega'
        """
    )
    op.create_index("ix_oc_recibidas_folio_fecha", "oc_recibidas",
                    ["tenant_id", "folio_externo", "fecha_entrega"])
    op.create_index("ix_oc_recibidas_archivo", "oc_recibidas",
                    ["tenant_id", "archivo_nombre"],
                    postgresql_where=sa.text("archivo_nombre IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_oc_recibidas_archivo", table_name="oc_recibidas")
    op.drop_index("ix_oc_recibidas_folio_fecha", table_name="oc_recibidas")
    op.drop_column("oc_recibidas", "fecha_entrega")
