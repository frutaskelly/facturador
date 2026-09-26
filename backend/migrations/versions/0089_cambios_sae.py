"""La cola de SAE también lleva CAMBIOS de producto, no sólo altas.

Hasta hoy la cola `solicitudes_alta_sae` solo creaba artículos. Con el
Facturador como único escritor de SAE (26-sep-2026, decisión del dueño: «el
Facturador debe poder escribir en el SAE y el bot delega») los cambios a un
artículo que ya existe —descripción, línea, unidad, esquema, clave SAT y
reactivarlo— entran por la MISMA cola, con las mismas reglas: una sola viva por
clave, reclamo excluyente, resultado por empresa y nunca un reintento.

`tipo` distingue las dos. El candado de «una viva por clave» pasa a ser por
(tenant, tipo, clave): un cambio pendiente no debe bloquear el alta de la misma
clave, ni al revés.

Revision ID: 0089_cambios_sae
Revises: 0088_cobranza_automatica
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0089_cambios_sae"
down_revision: Union[str, None] = "0088_cobranza_automatica"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTA = crear el artículo · CAMBIO = modificar uno que ya existe
    op.add_column("solicitudes_alta_sae",
                  sa.Column("tipo", sa.String(8), nullable=False, server_default="ALTA"))
    op.execute("DROP INDEX IF EXISTS uq_alta_sae_viva")
    op.execute(
        "CREATE UNIQUE INDEX uq_alta_sae_viva ON solicitudes_alta_sae (tenant_id, tipo, clave) "
        "WHERE estado IN ('PENDIENTE', 'EN_CURSO')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_alta_sae_viva")
    op.execute("DELETE FROM solicitudes_alta_sae WHERE tipo <> 'ALTA'")
    op.execute(
        "CREATE UNIQUE INDEX uq_alta_sae_viva ON solicitudes_alta_sae (tenant_id, clave) "
        "WHERE estado IN ('PENDIENTE', 'EN_CURSO')"
    )
    op.drop_column("solicitudes_alta_sae", "tipo")
