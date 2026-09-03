"""Clave SAE del cliente por sucursal (caso EHMO: dos listas, dos plazas).

Un cliente puede vivir en dos empresas SAE con claves de artículo distintas por
plaza (EHMO: "ESPINACAPZA" en Pachuca, "ESPINACASKG" en Villahermosa), pero
producto_clientes solo tenía UN lugar por (cliente, producto). Se agrega
`sucursal_id` opcional al mapeo: la fila genérica (NULL) sigue valiendo para
todas las plazas y una fila con sucursal la pisa SOLO en esa plaza — la misma
regla que ya usan cliente_externos (empresa SAE), las series y las listas.

El unique anterior (tenant, cliente, producto) impedía la fila genérica + la de
sucursal a la vez; se sustituye por dos índices únicos parciales, porque un
UNIQUE normal con sucursal_id NULL dejaría duplicar la genérica (NULL ≠ NULL).

Revision ID: 0067_clave_cliente_por_sucursal
Revises: 0066_vinculo_sucursal_default
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0067_clave_cliente_por_sucursal"
down_revision: Union[str, None] = "0066_vinculo_sucursal_default"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "producto_clientes",
        sa.Column("sucursal_id", UUID(as_uuid=True), nullable=True),
    )
    # CASCADE como PrecioOverride/ClienteSucursal (dato maestro anclado a la
    # plaza): si la sucursal se borra, la fila scoped muere en vez de colapsar
    # contra la genérica (SET NULL violaría el único parcial de abajo).
    op.create_foreign_key(
        "producto_clientes_sucursal_id_fkey",
        "producto_clientes",
        "sucursales",
        ["sucursal_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_producto_clientes_sucursal", "producto_clientes", ["sucursal_id"]
    )
    op.drop_constraint("uq_producto_cliente", "producto_clientes", type_="unique")
    op.create_index(
        "uq_producto_cliente_generico",
        "producto_clientes",
        ["tenant_id", "cliente_id", "producto_id"],
        unique=True,
        postgresql_where=sa.text("sucursal_id IS NULL"),
    )
    op.create_index(
        "uq_producto_cliente_sucursal",
        "producto_clientes",
        ["tenant_id", "cliente_id", "producto_id", "sucursal_id"],
        unique=True,
        postgresql_where=sa.text("sucursal_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_producto_cliente_sucursal", table_name="producto_clientes")
    op.drop_index("uq_producto_cliente_generico", table_name="producto_clientes")
    # Sin la columna, las filas scoped duplicarían (tenant, cliente, producto)
    # y el unique original no se podría restaurar: se descartan (son el dato
    # que esta migración introduce).
    op.execute("DELETE FROM producto_clientes WHERE sucursal_id IS NOT NULL")
    op.create_unique_constraint(
        "uq_producto_cliente",
        "producto_clientes",
        ["tenant_id", "cliente_id", "producto_id"],
    )
    op.drop_index("ix_producto_clientes_sucursal", table_name="producto_clientes")
    op.drop_constraint(
        "producto_clientes_sucursal_id_fkey", "producto_clientes", type_="foreignkey"
    )
    op.drop_column("producto_clientes", "sucursal_id")
