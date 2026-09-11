"""Correos destinatarios de facturas POR PROYECTO (ticket 86bbyveu1).

Las facturas de un mismo proyecto casi siempre van a las mismas personas, y
un mismo cliente cambia de destinatarios según el proyecto (EHMO: Hospitales
vs IMSS Bienestar). Teclearlos en cada envío es repetitivo y es donde se
equivoca uno de correo. La lista vive en el proyecto y el envío la SUGIERE
(prellenado editable): manda proyecto > cliente, y el usuario siempre puede
corregir antes de enviar.

Revision ID: 0073_proyecto_correos_facturas
Revises: 0072_oc_cambio_posterior
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0073_proyecto_correos_facturas"
down_revision: Union[str, None] = "0072_oc_cambio_posterior"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "proyectos",
        sa.Column(
            "correos_facturas",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("proyectos", "correos_facturas")
