"""Catálogo por cliente: cómo llama cada cliente a un producto del catálogo.

(cliente, producto) → codigo_cliente (NoIdentificacion) + nombre_cliente
(Descripcion del CFDI). Evita duplicar productos por cliente: el builder del
CFDI aplica el alias al timbrar.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040_producto_clientes"
down_revision: Union[str, None] = "0039_factura_sustitucion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "producto_clientes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cliente_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("producto_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("productos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("codigo_cliente", sa.String(50)),
        sa.Column("nombre_cliente", sa.String(254)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "cliente_id", "producto_id",
                            name="uq_producto_cliente"),
    )
    op.create_index("ix_producto_clientes_tenant", "producto_clientes", ["tenant_id"])
    op.create_index("ix_producto_clientes_cliente", "producto_clientes", ["cliente_id"])
    op.create_index("ix_producto_clientes_producto", "producto_clientes", ["producto_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON producto_clientes TO app_user")
    op.execute("ALTER TABLE producto_clientes ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON producto_clientes "
        "USING (tenant_id = public.current_tenant_id())"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON producto_clientes")
    op.execute("ALTER TABLE producto_clientes DISABLE ROW LEVEL SECURITY")
    op.drop_table("producto_clientes")
