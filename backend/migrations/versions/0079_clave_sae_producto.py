"""La clave de SAE vive en el PRODUCTO, no en cada cliente.

Decisión del dueño (18-sep-2026), después de medir el catálogo: el mismo
producto existía hasta tres veces como artículo distinto DENTRO de la misma
empresa de SAE —AJO era `AJO-FRUT-017` para Balles y Jubran, `AJOPRIMERAKG`
para EHMO y MAFAN, `AJOKG` para los de Chiapas—, y `producto_clientes` guardaba
2,527 filas para 1,206 productos: ~1,300 de ellas sólo repetían la misma clave
para otro cliente.

Gana la convención de EHMO/MAFAN (`AJOPRIMERAKG`, `ACELGASKG`): mueve $4.45M
contra $0.61M en 180 días y es la que ya está replicada en la empresa 03.

`productos.clave_sae` es esa clave única, la misma en todas las empresas de SAE.
El catálogo del cliente NO desaparece: sigue siendo la excepción que pisa a la
base (y el lugar donde vive cómo llama el cliente al producto, que es otra cosa
y va al CFDI). La resolución queda: plaza del cliente → genérica del cliente →
**clave base del producto**. Puramente aditiva: lo que hoy resuelve, resuelve
igual; lo que hoy no tiene clave, deja de estar sin clave.

El índice único es la regla del dueño hecha candado: dos productos no pueden
compartir clave. Dos que la compartan le mandan a SAE la misma línea dos veces.

Esta migración sólo crea la columna. Los datos los escribe el script
`scripts/backfill_clave_sae.py`, que se corre a mano y reporta antes de tocar.

Revision ID: 0079_clave_sae_producto
Revises: 0078_remision_impresa

OJO: PR #161 (bitácora de importación de productos) también estrena un 0078. El
segundo en fusionar repunta su `down_revision`, o alembic queda con dos cabezas.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0079_clave_sae_producto"
down_revision: Union[str, None] = "0078_remision_impresa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("productos", sa.Column("clave_sae", sa.String(length=50), nullable=True))
    # Único por tenant, normalizado igual que al comparar contra el espejo
    # (SAE guarda CVE_ART con relleno y el cruce falla por un espacio).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_producto_clave_sae
            ON productos (tenant_id, upper(btrim(clave_sae)))
         WHERE clave_sae IS NOT NULL AND deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_producto_clave_sae")
    op.drop_column("productos", "clave_sae")
