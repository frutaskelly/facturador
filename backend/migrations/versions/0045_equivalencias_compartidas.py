"""Un grupo y un punto de entrega pueden ser de VARIOS clientes.

Balles y Jubran son dos razones sociales de la misma operación: comparten el
grupo de WhatsApp, los puntos de entrega, la serie y la lista de precios. Con un
único (tenant, sistema, clave) eso no se podía expresar — el JID solo podía
apuntar a uno, y Jubran no podía tener su propio «PROCU» hacia su sucursal.

La regla queda partida en dos, según lo que la clave signifique:

  IDENTIFICAN al cliente (RFC, SAE, PROYECTO, NOMBRE) → una clave, un cliente.
  Un RFC que apuntara a dos razones sociales sería un error de captura.

  DAN CONTEXTO (WHATSAPP, UBICACION) → una clave puede repetirse por cliente.
  El grupo aporta CANDIDATOS («esta orden es de Balles o de Jubran, decide tú»),
  no una respuesta; y el punto de entrega resuelve la sucursal de CADA cliente
  que descargue ahí.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_equivalencias_compartidas"
down_revision: Union[str, None] = "0044_punto_entrega"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONTEXTO = "('WHATSAPP','UBICACION')"


def upgrade() -> None:
    op.drop_constraint(
        "uq_cliente_externo_tenant_sistema_clave", "cliente_externos", type_="unique"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_cliente_externo_identifica "
        "ON cliente_externos (tenant_id, sistema, clave_normalizada) "
        f"WHERE sistema NOT IN {_CONTEXTO}"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_cliente_externo_contexto "
        "ON cliente_externos (tenant_id, sistema, clave_normalizada, cliente_id) "
        f"WHERE sistema IN {_CONTEXTO}"
    )
    # La lista corta de clientes posibles cuando el grupo no alcanza a decidir.
    # Se guarda resuelta para que la bandeja la muestre sin recalcularla.
    op.add_column(
        "oc_recibidas",
        sa.Column("candidatos", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("oc_recibidas", "candidatos")
    op.execute("DROP INDEX IF EXISTS uq_cliente_externo_contexto")
    op.execute("DROP INDEX IF EXISTS uq_cliente_externo_identifica")
    op.create_unique_constraint(
        "uq_cliente_externo_tenant_sistema_clave",
        "cliente_externos",
        ["tenant_id", "sistema", "clave_normalizada"],
    )
