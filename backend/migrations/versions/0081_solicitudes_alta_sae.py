"""La cola de altas de producto en SAE (meta: WhatsApp → Facturador → SAE).

Hoy el bot inserta directo en INVE02 y solo en la empresa 02: el producto nace
en SAE y el Facturador se entera después por el espejo. Con esta tabla se
invierte —igual que se invirtió la ingesta de órdenes—: el producto nace en el
catálogo de aquí y la alta en SAE queda PEDIDA; el conector (el único que ve
SAE) la reclama y reporta qué creó en cada empresa.

El resultado se guarda POR EMPRESA porque **nunca se reintenta una escritura a
SAE**: un INSERT repetido duplica el producto. De ahí el estado PARCIAL, que no
es OK (falta trabajo) ni ERROR (algo sí se creó y no se puede repetir).

El índice único parcial es el candado de duplicados: una sola alta viva por
(tenant, clave). Dos «dale de alta AJOKG» seguidos por WhatsApp devuelven la
misma solicitud en vez de crear dos altas que insertarían dos veces.

Revision ID: 0081_solicitudes_alta_sae
Revises: 0080_precio_depositar
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0081_solicitudes_alta_sae"
down_revision: Union[str, None] = "0080_precio_depositar"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "solicitudes_alta_sae",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        # PENDIENTE → EN_CURSO (el conector la reclamó) → OK | PARCIAL | ERROR
        sa.Column("estado", sa.String(10), nullable=False, server_default="PENDIENTE"),
        # WHATSAPP = lo pidió el bot · UI = la pantalla de catálogo
        sa.Column("origen", sa.String(12), nullable=False, server_default="UI"),
        sa.Column("producto_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("productos.id", ondelete="SET NULL")),
        # La clave con la que nace en SAE (CVE_ART): la identidad de la alta
        sa.Column("clave", sa.String(20), nullable=False),
        # Lo que SAE necesita para el INSERT, completo: si el catálogo cambia
        # mañana, la alta que se aplicó es ESTA
        sa.Column("datos", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("empresas", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("solicitada_por", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("solicitada_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("iniciada_at", sa.DateTime(timezone=True)),
        sa.Column("terminada_at", sa.DateTime(timezone=True)),
        # Qué contestó SAE por empresa: la única fuente de qué se creó
        sa.Column("resultado", postgresql.JSONB()),
        sa.Column("motivo", sa.Text()),
    )
    op.create_index("ix_alta_sae_tenant", "solicitudes_alta_sae", ["tenant_id"])
    op.create_index("ix_alta_sae_producto", "solicitudes_alta_sae", ["producto_id"])
    op.create_index("ix_alta_sae_clave", "solicitudes_alta_sae", ["clave"])
    # Una sola alta VIVA por clave: el candado contra el doble INSERT
    op.execute(
        "CREATE UNIQUE INDEX uq_alta_sae_viva ON solicitudes_alta_sae (tenant_id, clave) "
        "WHERE estado IN ('PENDIENTE', 'EN_CURSO')"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON solicitudes_alta_sae TO app_user")
    op.execute("ALTER TABLE solicitudes_alta_sae ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON solicitudes_alta_sae "
        "USING (tenant_id = public.current_tenant_id())"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON solicitudes_alta_sae")
    op.drop_index("uq_alta_sae_viva", table_name="solicitudes_alta_sae")
    op.drop_index("ix_alta_sae_clave", table_name="solicitudes_alta_sae")
    op.drop_index("ix_alta_sae_producto", table_name="solicitudes_alta_sae")
    op.drop_index("ix_alta_sae_tenant", table_name="solicitudes_alta_sae")
    op.drop_table("solicitudes_alta_sae")
