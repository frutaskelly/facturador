"""Separar lo que el bot REPORTA de lo que el dueño DECIDE sobre un grupo.

`activo` venía del bot y se pisaba en cada sincronización, así que apagar un
grupo desde el Facturador no servía de nada: a los pocos minutos volvía a estar
prendido. Ahora son dos cosas distintas:

  reportado_activo — lo que dice la config del bot. Solo lo escribe la sync.
  activo           — lo que decidió el dueño AQUÍ. Manda sobre lo anterior.

Un grupo apagado en el Facturador sigue procesándose en Smart Supply (su Master
de Sheets no cambia); lo que deja de hacer es ensuciar la bandeja. Las órdenes
que lleguen de ahí se guardan igual, como DESCARTADAS y con el motivo escrito:
perder una orden porque alguien apagó un grupo sería peor que el problema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0048_grupo_activo_override"
down_revision: Union[str, None] = "0047_grupos_whatsapp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "grupos_whatsapp",
        sa.Column("reportado_activo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    # Lo que ya había venía del bot: se copia al campo nuevo para no arrancar
    # con todos los grupos apagados diciendo que el dueño lo decidió.
    op.execute("UPDATE grupos_whatsapp SET reportado_activo = activo")


def downgrade() -> None:
    op.drop_column("grupos_whatsapp", "reportado_activo")
