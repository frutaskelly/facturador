"""El directorio de grupos de WhatsApp, visible desde el Facturador.

Hasta ahora el Facturador solo veía JIDs sueltos dentro de las equivalencias:
sabía que «120363429673827293@g.us» era candidato de Balles y de Jubran, pero no
cómo se llama ese grupo, ni si es interno o del cliente, ni de qué operación es.
Todo eso vive en la config del bot, y sin ello la pantalla de Conexiones no puede
responder la pregunta que importa: qué grupo alimenta a qué.

El bot lo sincroniza con su propia clave de conexión. Es un espejo, no la fuente:
la verdad sigue siendo `sheets_config.json`; aquí se guarda para poder mostrarlo
y para cruzarlo con clientes, sucursales y series.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_grupos_whatsapp"
down_revision: Union[str, None] = "0046_almacen_por_cliente"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "grupos_whatsapp",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jid", sa.String(120), nullable=False),
        sa.Column("nombre", sa.String(254)),
        # interno = el equipo (hace de todo) · cliente = solo se le reciben PDFs
        sa.Column("rol", sa.String(12)),
        sa.Column("perfil", sa.String(40)),          # ehmo, villahermosa, …
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        # Lo que el bot reportó tal cual, por si mañana trae más campos.
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("sincronizado_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "jid", name="uq_grupo_whatsapp_tenant_jid"),
    )
    op.create_index("ix_grupos_whatsapp_tenant", "grupos_whatsapp", ["tenant_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON grupos_whatsapp TO app_user")
    op.execute("ALTER TABLE grupos_whatsapp ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON grupos_whatsapp "
        "USING (tenant_id = public.current_tenant_id())"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grupos_whatsapp")
    op.execute("ALTER TABLE grupos_whatsapp DISABLE ROW LEVEL SECURITY")
    op.drop_table("grupos_whatsapp")
