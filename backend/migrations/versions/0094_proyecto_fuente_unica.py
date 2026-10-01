"""El proyecto dice sus series y dónde se reporta: Catálogo → Proyectos manda

Revision ID: 0094_proyecto_fuente_unica
Revises: 0093_alias_con_unidad
Create Date: 2026-10-01

Decisión del dueño (1-oct-2026): el proyecto vive en un solo lugar y todo lo
demás —precios, reportes, cobranza— lo lee de ahí. Hasta hoy Reportes
clasificaba con un mapa serie→nombre escrito en el código, con nombres que no
cuadraban con el catálogo.

- `series`: las series de factura que son del proyecto (["ZEHMOTG"]). Una
  factura del espejo de SAE cae sola en su proyecto por la serie.
- `palabras_obs`: para la serie que comparten varios proyectos (ZMAFAN): si la
  observación de la factura dice alguna de estas palabras, es de este proyecto.
  El que comparte la serie SIN palabras es el que se queda con el resto.
- `reporta_en_id`: el proyecto que cobra con su propia lista pero se reporta
  dentro de otro (NERI y SEGURIDAD PÚBLICA → CERESOS).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0094_proyecto_fuente_unica"
down_revision: Union[str, None] = "0093_alias_con_unidad"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("proyectos", sa.Column("series", JSONB, nullable=False, server_default="[]"))
    op.add_column("proyectos", sa.Column("palabras_obs", JSONB, nullable=False, server_default="[]"))
    op.add_column(
        "proyectos",
        sa.Column(
            "reporta_en_id", UUID(as_uuid=True),
            sa.ForeignKey("proyectos.id", ondelete="SET NULL"), nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("proyectos", "reporta_en_id")
    op.drop_column("proyectos", "palabras_obs")
    op.drop_column("proyectos", "series")
