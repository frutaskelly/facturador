"""POS: etapas custom — `pos_etapa` pasa de 12 a 30 caracteres.

Las etapas propias del tenant ("verificacion", "empaque_frio"…) usan slugs de
hasta 30 caracteres; String(12) las truncaría.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035_pos_etapa_ancha"
down_revision: Union[str, None] = "0034_pos_base"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("remisiones", "pos_etapa", type_=sa.String(30))


def downgrade() -> None:
    op.alter_column("remisiones", "pos_etapa", type_=sa.String(12))
