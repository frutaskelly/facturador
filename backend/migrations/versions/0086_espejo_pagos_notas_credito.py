"""Espejo de los comprobantes de pago (REP) y las notas de crédito que emite SAE.

Hasta aquí el espejo traía sólo facturas: los REP que SAE timbra (FACTGxx) y
las notas de crédito (CFDI de egreso, aplicadas en CxC con el concepto 1002)
no llegaban, y la pestaña «Comprobantes de pago» de Reportes salía vacía
porque sólo veía los REP que timbra el propio Facturador — que ningún cliente
espejo usa (23-sep-2026).

Los REP del SAE entran en la MISMA tabla que los nativos, marcados con
`origen='ESPEJO_SAE'`: así Cobranza y Reportes los listan sin un segundo
camino. Tres cosas cambian para que quepan:

- El UNIQUE (tenant, serie, folio) pasa a valer sólo para los NATIVOS. SAE
  numera sus REP por empresa y casi siempre sin serie: el «11» de la 02 y el
  «11» de la 04 son comprobantes distintos. El espejo se ancla por
  (tenant, empresa, CVE_DOC), igual que SAE.
- `recibo_pago_facturas.factura_id` admite NULL, con `factura_ref` de
  respaldo: un REP puede abonar a una factura que el espejo no tiene (un
  cliente sin equivalencia) y el renglón no debe perderse.
- `saldo_anterior`/`saldo_insoluto` admiten NULL: un REP cancelado en SAE ya
  no conserva sus renglones en CxC.

Las notas de crédito van en tabla propia: no son facturas (restan) ni pagos.

Revision ID: 0086_espejo_pagos_notas_credito
Revises: 0085_clave_sae_compartida
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0086_espejo_pagos_notas_credito"
down_revision: Union[str, None] = "0085_clave_sae_compartida"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rls(tabla: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tabla} TO app_user")
    op.execute(f"ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {tabla} "
        "USING (tenant_id = public.current_tenant_id())"
    )


def upgrade() -> None:
    # ── REP: el espejo vive junto a los nativos ─────────────────────────────
    op.add_column("recibos_pago", sa.Column(
        "origen", sa.String(12), nullable=False, server_default="NATIVO"))
    op.add_column("recibos_pago", sa.Column("espejo_empresa", sa.String(4)))
    op.add_column("recibos_pago", sa.Column("espejo_cve_doc", sa.String(30)))
    op.alter_column("recibos_pago", "serie", type_=sa.String(20))
    op.drop_constraint("uq_recibo_tenant_serie_folio", "recibos_pago", type_="unique")
    op.execute(
        "CREATE UNIQUE INDEX uq_recibo_tenant_serie_folio ON recibos_pago "
        "(tenant_id, serie, folio) WHERE origen = 'NATIVO'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_recibo_espejo ON recibos_pago "
        "(tenant_id, espejo_empresa, espejo_cve_doc) WHERE origen = 'ESPEJO_SAE'"
    )
    op.create_index("ix_recibos_pago_fecha", "recibos_pago", ["tenant_id", "fecha_pago"])

    op.alter_column("recibo_pago_facturas", "factura_id", nullable=True)
    op.alter_column("recibo_pago_facturas", "saldo_anterior", nullable=True)
    op.alter_column("recibo_pago_facturas", "saldo_insoluto", nullable=True)
    op.add_column("recibo_pago_facturas", sa.Column("factura_ref", sa.String(40)))

    # ── Notas de crédito (CFDI de egreso) ───────────────────────────────────
    op.create_table(
        "notas_credito",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cliente_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clientes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("serie", sa.String(20), nullable=False, server_default=""),
        sa.Column("folio", sa.Integer(), nullable=False),
        sa.Column("fecha", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("moneda", sa.String(3), nullable=False, server_default="MXN"),
        # VIGENTE | CANCELADA — la vigencia la decide el SAT (CFDI.FECHA_CANCELA)
        sa.Column("estado", sa.String(10), nullable=False, server_default="VIGENTE"),
        sa.Column("uuid", sa.String(36)),
        sa.Column("fecha_cancelacion", sa.DateTime(timezone=True)),
        sa.Column("origen", sa.String(12), nullable=False, server_default="ESPEJO_SAE"),
        sa.Column("espejo_empresa", sa.String(4)),
        sa.Column("espejo_cve_doc", sa.String(30)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_notas_credito_tenant", "notas_credito", ["tenant_id"])
    op.create_index("ix_notas_credito_cliente", "notas_credito", ["cliente_id"])
    op.create_index("ix_notas_credito_fecha", "notas_credito", ["tenant_id", "fecha"])
    op.execute(
        "CREATE UNIQUE INDEX uq_nota_credito_espejo ON notas_credito "
        "(tenant_id, espejo_empresa, espejo_cve_doc)"
    )
    _rls("notas_credito")

    op.create_table(
        "nota_credito_facturas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nota_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("notas_credito.id", ondelete="CASCADE"), nullable=False),
        # NULL cuando la factura no está en el espejo; factura_ref la nombra igual
        sa.Column("factura_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("facturas.id", ondelete="SET NULL")),
        sa.Column("factura_ref", sa.String(40)),
        sa.Column("importe", sa.Numeric(18, 4), nullable=False),
    )
    op.create_index("ix_nc_facturas_tenant", "nota_credito_facturas", ["tenant_id"])
    op.create_index("ix_nc_facturas_nota", "nota_credito_facturas", ["nota_id"])
    op.create_index("ix_nc_facturas_factura", "nota_credito_facturas", ["factura_id"])
    _rls("nota_credito_facturas")


def downgrade() -> None:
    for t in ("nota_credito_facturas", "notas_credito"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.drop_table(t)
    op.execute("DELETE FROM recibo_pago_facturas WHERE recibo_id IN "
               "(SELECT id FROM recibos_pago WHERE origen = 'ESPEJO_SAE')")
    op.execute("DELETE FROM recibos_pago WHERE origen = 'ESPEJO_SAE'")
    op.drop_column("recibo_pago_facturas", "factura_ref")
    op.alter_column("recibo_pago_facturas", "saldo_insoluto", nullable=False)
    op.alter_column("recibo_pago_facturas", "saldo_anterior", nullable=False)
    op.alter_column("recibo_pago_facturas", "factura_id", nullable=False)
    op.drop_index("ix_recibos_pago_fecha", table_name="recibos_pago")
    op.execute("DROP INDEX IF EXISTS uq_recibo_espejo")
    op.execute("DROP INDEX IF EXISTS uq_recibo_tenant_serie_folio")
    op.create_unique_constraint(
        "uq_recibo_tenant_serie_folio", "recibos_pago", ["tenant_id", "serie", "folio"])
    op.alter_column("recibos_pago", "serie", type_=sa.String(10))
    op.drop_column("recibos_pago", "espejo_cve_doc")
    op.drop_column("recibos_pago", "espejo_empresa")
    op.drop_column("recibos_pago", "origen")
