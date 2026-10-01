"""El proyecto dice de qué almacén sale su mercancía

Revision ID: 0095_almacen_del_proyecto
Revises: 0094_proyecto_fuente_unica
Create Date: 2026-10-01

Decisión del dueño (1-oct-2026): «ALMACÉN es de donde sale la mercancía del
proyecto». Entra en la cascada de `resolver_almacen` justo después de la
elección manual: manual → proyecto → plaza → cliente → predeterminado. NULL =
el proyecto no fija almacén y decide lo de siempre.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0095_almacen_del_proyecto"
down_revision: Union[str, None] = "0094_proyecto_fuente_unica"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "proyectos",
        sa.Column(
            "almacen_id", UUID(as_uuid=True),
            sa.ForeignKey("almacenes.id", ondelete="SET NULL"), nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("proyectos", "almacen_id")
