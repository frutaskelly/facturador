"""El alias de producto gana alcance: global por defecto, por cliente en conflicto

Revision ID: 0053_alias_por_cliente
Revises: 0052_remision_su_pedido
Create Date: 2026-08-28

`producto_alias` era único por (tenant, alias_normalizado): un texto solo podía
apuntar a UN producto en todo el tenant, y "LIMON" no podía ser LIMON SIN
SEMILLA para EHMO Pachuca y LIMON AGRIO para Villahermosa a la vez. La regla de
oro del bot de WhatsApp ("el vocabulario de un cliente no cruza al de otro") se
adopta con matiz: el alias sigue siendo GLOBAL por defecto — así Balles, Jubran
y MAFAN comparten vocabulario gratis — y solo cuando el mismo texto debe
significar cosas distintas se crea un alias con cliente (y opcionalmente
sucursal, para el caso EHMO Pachuca vs Villahermosa: misma razón social, dos
vocabularios).

La unicidad pasa a (tenant, cliente, sucursal, alias_normalizado) con NULL
tratado como valor (COALESCE a UUID cero): un global y un por-cliente del mismo
texto conviven; dos globales iguales no.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0053_alias_por_cliente"
down_revision: Union[str, None] = "0052_remision_su_pedido"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CERO = "'00000000-0000-0000-0000-000000000000'::uuid"


def upgrade() -> None:
    op.add_column(
        "producto_alias",
        sa.Column("cliente_id", UUID(as_uuid=True),
                  sa.ForeignKey("clientes.id", ondelete="CASCADE"), nullable=True),
    )
    op.add_column(
        "producto_alias",
        sa.Column("sucursal_id", UUID(as_uuid=True),
                  sa.ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index("ix_producto_alias_cliente", "producto_alias", ["cliente_id"],
                    postgresql_where=sa.text("cliente_id IS NOT NULL"))
    op.drop_constraint("uq_alias_tenant_norm", "producto_alias", type_="unique")
    # Índice único funcional (no constraint): COALESCE trata NULL como valor,
    # sin depender de NULLS NOT DISTINCT (PostgreSQL 15+).
    op.create_index(
        "uq_alias_tenant_alcance_norm",
        "producto_alias",
        [sa.text("tenant_id"), sa.text(f"COALESCE(cliente_id, {_CERO})"),
         sa.text(f"COALESCE(sucursal_id, {_CERO})"), sa.text("alias_normalizado")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_alias_tenant_alcance_norm", table_name="producto_alias")
    # Volver al único global implica que no haya alias con alcance duplicando
    # texto; se eliminan primero los con alcance (son derivables de nuevo).
    op.execute("DELETE FROM producto_alias WHERE cliente_id IS NOT NULL OR sucursal_id IS NOT NULL")
    op.create_unique_constraint("uq_alias_tenant_norm", "producto_alias",
                                ["tenant_id", "alias_normalizado"])
    op.drop_index("ix_producto_alias_cliente", table_name="producto_alias")
    op.drop_column("producto_alias", "sucursal_id")
    op.drop_column("producto_alias", "cliente_id")
