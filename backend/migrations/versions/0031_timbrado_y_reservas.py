"""Bitácora de timbrado + adiós cubeta de reservas (decisiones 2026-07-29 #1 y #2).

#1: `timbrado_intentos` — se persiste (commit propio) ANTES de llamar al PAC y
se marca al terminar. Un PENDIENTE fresco actúa de mutex; uno viejo dispara la
reconciliación contra Facturama para nunca timbrar dos veces la misma factura.

#2: confirmar una remisión ahora es salida directa de `disponible` (el camión
salió); `cantidad_reservada` deja de usarse. Limpieza única: la cubeta se pone
en 0 (era la suma histórica de ventas, no un apartado real) y se quitan los
stamps huérfanos de líneas cuyas remisiones ya no respaldan una salida viva
(BORRADOR/CANCELADA) — los de CONFIRMADA/FACTURADA se conservan porque son los
que permiten restituir el inventario al cancelar.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031_timbrado_y_reservas"
down_revision: Union[str, None] = "0030_factura_directa_inventario"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── #1 bitácora de intentos de timbrado ─────────────────────────────────
    op.create_table(
        "timbrado_intentos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("factura_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("facturas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("estado", sa.String(10), nullable=False, server_default="PENDIENTE"),
        sa.Column("detalle", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_timbrado_intentos_tenant", "timbrado_intentos", ["tenant_id"])
    op.create_index("ix_timbrado_intentos_factura", "timbrado_intentos", ["factura_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON timbrado_intentos TO app_user")
    op.execute("ALTER TABLE timbrado_intentos ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON timbrado_intentos "
        "USING (tenant_id = public.current_tenant_id())"
    )

    # ─── #2 limpieza única de la cubeta de reservas ──────────────────────────
    op.execute("UPDATE lotes_inventario SET cantidad_reservada = 0 WHERE cantidad_reservada <> 0")
    op.execute(
        """
        UPDATE lineas_remision lr
           SET lote_id = NULL, cantidad_surtida = NULL
          FROM remisiones r
         WHERE lr.remision_id = r.id
           AND r.estado IN ('BORRADOR', 'CANCELADA')
           AND lr.lote_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # La limpieza de reservas/stamps no es reversible (era estado histórico).
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON timbrado_intentos")
    op.execute("ALTER TABLE timbrado_intentos DISABLE ROW LEVEL SECURITY")
    op.drop_table("timbrado_intentos")
