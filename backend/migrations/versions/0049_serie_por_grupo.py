"""Un cliente puede usar VARIAS series, según el grupo por el que entre.

`clientes.serie_factura_id` es una sola, y no alcanza: en SAE el cliente 5 (EHMO)
factura hospitales con ZEHMOHOS y costales con ZEHMOFAC, y el grupo interno de
Pachuca declara tres series a la vez (ZEHMOHOS, ZMAFAN, ZECA). La serie no es
una propiedad del cliente sino de la operación por la que llega el pedido.

Se guarda donde ya vive el resto del contexto del grupo: la fila WHATSAPP de
`cliente_externos`, junto a la sucursal por defecto. Vacío = hereda la del
cliente, y la del cliente sigue siendo el default de todo lo demás.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0049_serie_por_grupo"
down_revision: Union[str, None] = "0048_grupo_activo_override"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for col in ("serie_factura_id", "serie_remision_id"):
        op.add_column(
            "cliente_externos",
            sa.Column(
                col,
                postgresql.UUID(as_uuid=True),
                # SET NULL: borrar una serie no puede llevarse la equivalencia
                # del grupo. Se queda sin override y hereda la del cliente.
                sa.ForeignKey("series.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    for col in ("serie_remision_id", "serie_factura_id"):
        op.drop_column("cliente_externos", col)
