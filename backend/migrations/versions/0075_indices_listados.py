"""Índices para los ORDER BY de los tres listados principales + cobranza.

Los listados de remisiones (fecha_remision DESC), facturas (fecha DESC,
folio DESC) y bandeja de OC (recibida_at DESC) ordenan sin índice: cada
página 1 es un scan + sort de toda la tabla del tenant, y el count() de
paginate lo repite. Compuestos con tenant_id porque RLS filtra por él
siempre; parciales sobre deleted_at IS NULL donde el listado lo filtra.

El de cobranza cubre el filtro más selectivo del módulo (facturas PPD
timbradas con saldo), que corre 4 veces por pantalla de estado de cuenta
(JSON, PDF, XLSX y envío) y a diario por cliente.

Sin CONCURRENTLY a propósito (Alembic corre en transacción): son tablas
de miles de filas, el candado dura milisegundos.

Revision ID: 0075_indices_listados
Revises: 0074_trgm_sat_prodserv
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0075_indices_listados"
down_revision: Union[str, None] = "0074_trgm_sat_prodserv"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_remisiones_tenant_fecha",
        "remisiones",
        ["tenant_id", sa.text("fecha_remision DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_facturas_tenant_fecha",
        "facturas",
        ["tenant_id", sa.text("fecha DESC"), sa.text("folio DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_oc_recibidas_tenant_recibida",
        "oc_recibidas",
        ["tenant_id", sa.text("recibida_at DESC")],
    )
    op.create_index(
        "ix_facturas_ppd_pendientes",
        "facturas",
        ["tenant_id", "cliente_id", "fecha"],
        postgresql_where=sa.text(
            "deleted_at IS NULL AND estado = 'TIMBRADA' "
            "AND metodo_pago = 'PPD' AND saldo_insoluto > 0"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_facturas_ppd_pendientes", table_name="facturas")
    op.drop_index("ix_oc_recibidas_tenant_recibida", table_name="oc_recibidas")
    op.drop_index("ix_facturas_tenant_fecha", table_name="facturas")
    op.drop_index("ix_remisiones_tenant_fecha", table_name="remisiones")
