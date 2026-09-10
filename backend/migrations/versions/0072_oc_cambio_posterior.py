"""La OC cambió después de volverse remisión: detectarlo en vez de callarlo.

Un reenvío de la misma orden es idempotente por `origen_externo`, y cuando ya
generó su remisión la ingesta NO toca nada — correcto: una remisión capturada no
se pisa sola. Pero tampoco avisaba, y ahí se abre la divergencia real: el Master
se queda con la versión nueva, la remisión con la vieja, y nadie se entera hasta
que el cliente reclama. Mismo patrón que dejó 9 órdenes fuera de la bandeja el
3-sep-2026: un camino que no falla, simplemente no dice nada.

`payload` sigue congelado con la versión que generó la remisión (no cambia su
significado). La versión posterior vive aparte, con el diff calculado y su
propio ciclo abierto/cerrado — no leído/no leído: esto se RESUELVE, y queda
quién y qué decidió. Un aviso leído y no atendido se ve igual que uno atendido.

Revision ID: 0072_oc_cambio_posterior
Revises: 0071_factura_su_pedido
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0072_oc_cambio_posterior"
down_revision: Union[str, None] = "0071_factura_su_pedido"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "oc_recibidas",
        sa.Column("payload_nuevo", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "oc_recibidas",
        sa.Column("cambio_detectado_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "oc_recibidas",
        sa.Column("cambio_detalle", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "oc_recibidas",
        sa.Column("cambio_resuelto_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "oc_recibidas",
        sa.Column("cambio_resuelto_por", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "oc_recibidas",
        sa.Column("cambio_resuelto_nota", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_oc_recibidas_cambio_resuelto_por",
        "oc_recibidas",
        "users",
        ["cambio_resuelto_por"],
        ["id"],
        ondelete="SET NULL",
    )
    # Índice parcial: la única consulta caliente es "las que siguen abiertas".
    # Son un puñado entre miles de órdenes, así que el índice cabe en nada y
    # el filtro de la bandeja no barre la tabla.
    op.create_index(
        "ix_oc_recibidas_cambio_abierto",
        "oc_recibidas",
        ["tenant_id", "cambio_detectado_at"],
        postgresql_where=sa.text("cambio_detectado_at IS NOT NULL AND cambio_resuelto_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_oc_recibidas_cambio_abierto", table_name="oc_recibidas")
    op.drop_constraint(
        "fk_oc_recibidas_cambio_resuelto_por", "oc_recibidas", type_="foreignkey"
    )
    for col in ("cambio_resuelto_nota", "cambio_resuelto_por", "cambio_resuelto_at",
                "cambio_detalle", "cambio_detectado_at", "payload_nuevo"):
        op.drop_column("oc_recibidas", col)
