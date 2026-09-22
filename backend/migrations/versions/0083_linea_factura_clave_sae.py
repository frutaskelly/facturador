"""La clave de SAE de cada partida reflejada.

El espejo usaba la CVE_ART solo para cruzar con un producto y la tiraba. Cuando
cruzaba no dolía —el producto la tiene—, pero 7,227 de las 126,136 partidas
reflejadas NO cruzaron: de ésas quedaba una descripción suelta y nadie podía
decir de qué artículo hablaban. Justo las que hay que revisar.

Guardarla es además lo que permite que el bot conteste de facturas sin
preguntarle a SAE (meta: «WhatsApp habla únicamente con el Facturador»): sus
comandos hablan en claves de SAE, no en claves del SAT.

Revision ID: 0083_linea_factura_clave_sae
Revises: 0082_producto_alta_sae
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0083_linea_factura_clave_sae"
down_revision: Union[str, None] = "0082_producto_alta_sae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lineas_factura", sa.Column("clave_sae", sa.String(30)))
    # Índice parcial: se consulta para buscar partidas por clave, y la mayoría
    # de las líneas nativas no la tienen.
    op.execute("CREATE INDEX ix_lineas_factura_clave_sae ON lineas_factura (clave_sae) "
               "WHERE clave_sae IS NOT NULL")


def downgrade() -> None:
    op.drop_index("ix_lineas_factura_clave_sae", table_name="lineas_factura")
    op.drop_column("lineas_factura", "clave_sae")
