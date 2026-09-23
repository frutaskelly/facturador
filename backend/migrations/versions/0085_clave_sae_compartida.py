"""Una clave de SAE puede ampararse varios productos del Facturador.

Corrige la regla de la 0079. El dueño (23-sep-2026): la clave de SAE se puede
enlazar con varios productos de remisión, pero no puede haber productos de
remisión sin clave. El índice único lo impedía — al resolver la RZEHMOVH248
«CEBOLLABLANCAKG ya es de CEBOLLA BLANCA» bloqueaba guardar la clave a otra
CEBOLLA BLANCA —, y el caso es legítimo: dos productos del catálogo que en SAE
son el mismo artículo (variantes de nombre, una presentación distinta).

Que dos partidas lleven la misma CVE_ART no rompe el Excel de SAE: cada partida
es su propio renglón del documento.

Se queda un índice NORMAL con la misma expresión: los cruces clave → producto
(enlace de vocabulario, impuestos por clave, categoría del bot) siguen buscando
por ahí.

Revision ID: 0085_clave_sae_compartida
Revises: 0084_tickets
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0085_clave_sae_compartida"
down_revision: Union[str, None] = "0084_tickets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_producto_clave_sae")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_producto_clave_sae
            ON productos (tenant_id, upper(btrim(clave_sae)))
         WHERE clave_sae IS NOT NULL AND deleted_at IS NULL
        """
    )


def downgrade() -> None:
    # Volver al único falla si ya hay claves compartidas: es a propósito, no se
    # le quita la clave a nadie para poder bajar.
    op.execute("DROP INDEX IF EXISTS ix_producto_clave_sae")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_producto_clave_sae
            ON productos (tenant_id, upper(btrim(clave_sae)))
         WHERE clave_sae IS NOT NULL AND deleted_at IS NULL
        """
    )
