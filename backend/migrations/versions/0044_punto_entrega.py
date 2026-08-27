"""Punto de entrega de la orden — a dónde se descarga, no a quién se factura.

Corrección de modelo (27-ago-2026, dueño): los hospitales y planteles NO son
sucursales. Son puntos de entrega (o de descarga: es lo mismo) que pertenecen a
una sucursal — las 24 ubicaciones de Tabasco cuelgan de la sucursal «Tabasco» de
EHMO. Y el dato que el negocio necesita leer viaja en las OBSERVACIONES de la
remisión y de la factura, no en un catálogo.

Va en columna propia y no dentro de `payload` porque se corrige a mano en la
bandeja: `payload` es la evidencia de lo que decía el documento y no se toca.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044_punto_entrega"
down_revision: Union[str, None] = "0043_conexiones"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("oc_recibidas", sa.Column("punto_entrega", sa.String(254)))
    # Backfill: lo que ya estaba parseado en el payload de las órdenes que entraron.
    op.execute(
        "UPDATE oc_recibidas SET punto_entrega = NULLIF(TRIM(payload->>'ubicacion'), '') "
        "WHERE punto_entrega IS NULL"
    )


def downgrade() -> None:
    op.drop_column("oc_recibidas", "punto_entrega")
