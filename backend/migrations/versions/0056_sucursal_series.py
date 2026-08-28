"""Series DISPONIBLES por sucursal (una o más), pedido del dueño 28-ago-2026.

Una sucursal puede operar con varias series de factura y de remisión (EHMO
Pachuca factura hospitales con ZEHMOHOS y costales con ZEHMOFAC desde la misma
plaza). La serie DEFAULT sigue viviendo en `sucursales.serie_factura_id` /
`serie_remision_id` — la resolución de folios no cambia; esta tabla registra
el ABANICO completo, que es lo que los selectores de emisión y la pantalla de
Conexiones ofrecen para esa sucursal.

Revision ID: 0056_sucursal_series
Revises: 0055_facturas_espejo_sae
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0056_sucursal_series"
down_revision: Union[str, None] = "0055_facturas_espejo_sae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sucursal_series",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("sucursal_id", UUID(as_uuid=True),
                  sa.ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("serie_id", UUID(as_uuid=True),
                  sa.ForeignKey("series.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("sucursal_id", "serie_id", name="uq_sucursal_serie"),
    )
    # RLS y grants con la MISMA plantilla que el resto de tablas por tenant.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON sucursal_series TO app_user")
    op.execute("ALTER TABLE sucursal_series ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON sucursal_series
            USING (tenant_id = public.current_tenant_id())
        """
    )
    # Las defaults existentes también son "disponibles": se siembran solas.
    op.execute(
        """
        INSERT INTO sucursal_series (tenant_id, sucursal_id, serie_id)
        SELECT tenant_id, id, serie_factura_id FROM sucursales
         WHERE serie_factura_id IS NOT NULL AND deleted_at IS NULL
        UNION
        SELECT tenant_id, id, serie_remision_id FROM sucursales
         WHERE serie_remision_id IS NOT NULL AND deleted_at IS NULL
        ON CONFLICT ON CONSTRAINT uq_sucursal_serie DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("sucursal_series")
