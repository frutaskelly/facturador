"""Facturas espejo de SAE (fase espejo de la migración del Master).

Decisión del dueño (2026-08-28, PLAN-migracion-master-facturador.md §1.4): las
facturas que SAE emite se guardan como facturas REALES del Facturador — para
estados de cuenta, reportes y adaptación del equipo — pero con candados duros:
una factura espejo NUNCA llama al PAC (ni timbrar, ni cancelar, ni REP).

Tres columnas:

- facturas.origen ('NATIVA' | 'ESPEJO_SAE'): de dónde nació. Es el candado: los
  endpoints que hablan con Facturama rechazan todo lo que no sea NATIVA.
- clientes.espejo_sae: mientras el cliente esté "en espejo", crear facturas
  nativas para él devuelve 409 — su facturación vive en SAE y duplicarla sería
  un doble CFDI ante el SAT. El corte de la migración es POR CLIENTE: se apaga
  este switch cliente por cliente.
- lineas_factura.producto_id se vuelve NULABLE: una factura de SAE puede traer
  claves (CVE_ART) que el catálogo del Facturador todavía no conoce; el espejo
  guarda la línea con su descripción tal cual en vez de perderla. Las facturas
  NATIVAS siguen creándose siempre con producto (lo exige su schema de entrada).

Revision ID: 0055_facturas_espejo_sae
Revises: 0054_rls_tablas_globales
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0055_facturas_espejo_sae"
down_revision: Union[str, None] = "0054_rls_tablas_globales"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "facturas",
        sa.Column("origen", sa.String(12), nullable=False, server_default="NATIVA"),
    )
    # El listado de facturas filtra/badgea por origen; con el espejo global el
    # tenant tendrá miles de filas ESPEJO_SAE conviviendo con las nativas.
    op.create_index("ix_facturas_tenant_origen", "facturas", ["tenant_id", "origen"])
    op.add_column(
        "clientes",
        sa.Column("espejo_sae", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.alter_column("lineas_factura", "producto_id", nullable=True)


def downgrade() -> None:
    op.alter_column("lineas_factura", "producto_id", nullable=False)
    op.drop_column("clientes", "espejo_sae")
    op.drop_index("ix_facturas_tenant_origen", table_name="facturas")
    op.drop_column("facturas", "origen")
