"""Mini Conta: una clave por cuenta, cada una con su alcance.

Hasta aquí la empresa tenía UNA clave Mini Conta y generar otra revocaba la
anterior. Pero cada cuenta de Mini Conta es un cliente aparte (Kelly Chiapas,
Kelly Tabasco…): si Tabasco generaba la suya, Chiapas se quedaba sin conexión,
y la única clave podía leer todas las plazas.

- El índice «una viva por tipo» ya no aplica a MINI_CONTA; en su lugar, dos
  conexiones vivas de Mini Conta no pueden llamarse igual (el nombre es la
  cuenta de Mini Conta y es lo que la pantalla muestra).
- `alcance` (JSONB) dice qué comparte cada clave de Mini Conta:
  `{"series": [...], "clientes": null | [...], "catalogo": bool}`. NULL = la
  clave de antes, sin límite, hasta que el dueño le ponga uno en la pantalla.

Revision ID: 0092_mini_conta_por_cuenta
Revises: 0091_cierra_respaldo_codigo_sae
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0092_mini_conta_por_cuenta"
down_revision: Union[str, None] = "0091_cierra_respaldo_codigo_sae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conexiones", sa.Column("alcance", postgresql.JSONB()))
    op.execute("DROP INDEX IF EXISTS uq_conexion_viva_por_tipo")
    op.execute(
        "CREATE UNIQUE INDEX uq_conexion_viva_por_tipo ON conexiones (tenant_id, tipo) "
        "WHERE estado <> 'REVOCADA' AND tipo <> 'MINI_CONTA'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_conexion_mini_conta_nombre "
        "ON conexiones (tenant_id, lower(nombre)) "
        "WHERE estado <> 'REVOCADA' AND tipo = 'MINI_CONTA'"
    )


def downgrade() -> None:
    # Volver a «una viva por tipo» truena si quedaron varias de Mini Conta: se
    # dejan viva solo la más reciente de cada empresa.
    op.execute("""
        UPDATE conexiones c SET estado = 'REVOCADA', revocada_at = now()
         WHERE c.tipo = 'MINI_CONTA' AND c.estado <> 'REVOCADA'
           AND EXISTS (
               SELECT 1 FROM conexiones o
                WHERE o.tenant_id = c.tenant_id AND o.tipo = 'MINI_CONTA'
                  AND o.estado <> 'REVOCADA' AND o.created_at > c.created_at)
    """)
    op.execute("DROP INDEX IF EXISTS uq_conexion_mini_conta_nombre")
    op.execute("DROP INDEX IF EXISTS uq_conexion_viva_por_tipo")
    op.execute(
        "CREATE UNIQUE INDEX uq_conexion_viva_por_tipo ON conexiones (tenant_id, tipo) "
        "WHERE estado <> 'REVOCADA'"
    )
    op.drop_column("conexiones", "alcance")
