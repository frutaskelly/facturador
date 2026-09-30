"""El vocabulario traduce también la unidad: (texto + unidad OC) → (producto + unidad sistema)

Revision ID: 0093_alias_con_unidad
Revises: 0092_mini_conta_por_cuenta
Create Date: 2026-09-30

Regla del dueño (30-sep-2026): un producto (un SKU) tiene variantes por unidad
—SANDIA en KILO sale como SANDIAKG, en PIEZA como SANDIAPZ, cada una con su
precio— y el vocabulario tiene que decir en cuál de las dos entra la partida.

- `unidad_oc`: la unidad TAL COMO la escribe la orden, normalizada (sin
  espacios, mayúsculas: «Pz.» → «PZ»). NULL = la orden no trae unidad, o la
  trae en el texto («SANDIA PZA»).
- `presentacion`: la unidad del SISTEMA a la que se traduce (una presentación
  del producto). NULL en los renglones de antes: sólo dicen qué producto es.

La unicidad suma `unidad_oc`: «SANDIA | KG» y «SANDIA | PZ» conviven en el
mismo alcance. Los renglones de hoy quedan con unidad_oc NULL y funcionan igual.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0093_alias_con_unidad"
down_revision: Union[str, None] = "0092_mini_conta_por_cuenta"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CERO = "'00000000-0000-0000-0000-000000000000'::uuid"


def upgrade() -> None:
    op.add_column("producto_alias", sa.Column("unidad_oc", sa.String(20), nullable=True))
    op.add_column("producto_alias", sa.Column("presentacion", sa.String(20), nullable=True))
    op.drop_index("uq_alias_tenant_alcance_norm", table_name="producto_alias")
    op.create_index(
        "uq_alias_tenant_alcance_norm",
        "producto_alias",
        [sa.text("tenant_id"), sa.text(f"COALESCE(cliente_id, {_CERO})"),
         sa.text(f"COALESCE(sucursal_id, {_CERO})"), sa.text("alias_normalizado"),
         sa.text("COALESCE(unidad_oc, '')")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_alias_tenant_alcance_norm", table_name="producto_alias")
    # Sin la unidad en la llave, dos renglones del mismo texto chocarían: se
    # quedan sólo los que no traen unidad de la OC.
    op.execute("DELETE FROM producto_alias WHERE unidad_oc IS NOT NULL")
    op.create_index(
        "uq_alias_tenant_alcance_norm",
        "producto_alias",
        [sa.text("tenant_id"), sa.text(f"COALESCE(cliente_id, {_CERO})"),
         sa.text(f"COALESCE(sucursal_id, {_CERO})"), sa.text("alias_normalizado")],
        unique=True,
    )
    op.drop_column("producto_alias", "presentacion")
    op.drop_column("producto_alias", "unidad_oc")
