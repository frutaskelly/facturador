"""Bandeja de OC + equivalencias de cliente (ingesta desde WhatsApp/correo).

Tres piezas que llegan juntas porque sin las tres no se puede recibir una orden
de compra de forma desatendida:

1. `cliente_externos` — el mismo patrón de `producto_alias`, pero para clientes:
   una clave de un sistema externo (RFC impreso en el PDF, clave de SAE,
   proyecto, ubicación, grupo de WhatsApp) → cliente (y opcionalmente su
   sucursal). Cierra las cinco fuentes de verdad que hoy viven regadas en el bot.

2. `oc_recibidas` — la BANDEJA. Toda orden que entra aterriza aquí con su canal
   y su archivo, se le resuelve el cliente, y de ahí nace la remisión. Lo que no
   se pudo resolver NO se descarta ni se adivina: queda PENDIENTE para que un
   humano lo cierre desde la UI.

3. `remisiones.origen_externo` — idempotencia. Sin esto, un timeout de red a las
   3am hace que el reintento cree una remisión duplicada Y queme un folio de la
   serie (el mismo dolor que ya se resolvió en el timbrado con OrderNumber).

De paso: índice único PARCIAL (tenant, cliente, codigo) sobre las sucursales
VIVAS. El generador de códigos SUC-NN no está serializado, así que dos altas
simultáneas del mismo cliente podían dejar dos sucursales con el mismo código en
silencio — y ese código es justamente la llave con la que el bot cruza la
ubicación. Parcial y no total porque `sucursales` tiene borrado lógico: en la BD
ya conviven dos SUC-01 del mismo cliente, una de ellas borrada, y una sucursal
dada de baja no puede reservar su código para siempre.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_bandeja_oc_y_equivalencias"
down_revision: Union[str, None] = "0041_catalogo_sat_y_listas"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. equivalencias de cliente ──────────────────────────────────────────
    op.create_table(
        "cliente_externos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        # RFC | SAE | PROYECTO | UBICACION | WHATSAPP | NOMBRE
        sa.Column("sistema", sa.String(16), nullable=False),
        sa.Column("clave", sa.String(254), nullable=False),           # tal como venía
        sa.Column("clave_normalizada", sa.String(254), nullable=False),
        sa.Column("cliente_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False),
        # NULL = la clave solo determina el cliente (p. ej. un RFC), no el destino.
        sa.Column("sucursal_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sucursales.id", ondelete="SET NULL")),
        sa.Column("origen", sa.String(12), nullable=False, server_default="MANUAL"),   # MANUAL|BOT|IMPORT|IA
        sa.Column("confianza", sa.String(10), nullable=False, server_default="CONFIRMADA"),  # CONFIRMADA|SUGERIDA
        sa.Column("notas", sa.Text()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "sistema", "clave_normalizada",
                            name="uq_cliente_externo_tenant_sistema_clave"),
    )
    op.create_index("ix_cliente_externos_tenant", "cliente_externos", ["tenant_id"])
    op.create_index("ix_cliente_externos_cliente", "cliente_externos", ["cliente_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON cliente_externos TO app_user")
    op.execute("ALTER TABLE cliente_externos ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON cliente_externos "
        "USING (tenant_id = public.current_tenant_id())"
    )

    # ── 2. bandeja de órdenes de compra ──────────────────────────────────────
    op.create_table(
        "oc_recibidas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("canal", sa.String(20), nullable=False),            # WHATSAPP|EMAIL|MANUAL|API
        # Ancla de idempotencia: 'WA:<jid>:<folio>' / 'MAIL:<message-id>'.
        sa.Column("origen_externo", sa.String(120), nullable=False),
        sa.Column("folio_externo", sa.String(60)),                    # folio de la OC del cliente
        sa.Column("remitente", sa.String(254)),                       # grupo de WhatsApp / correo
        sa.Column("archivo_nombre", sa.String(254)),
        sa.Column("archivo_url", sa.Text()),                          # OneDrive / Drive
        sa.Column("recibida_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # PENDIENTE | ASIGNADA | DESCARTADA
        sa.Column("estado", sa.String(16), nullable=False, server_default="PENDIENTE"),
        sa.Column("motivo", sa.Text()),                               # por qué quedó pendiente
        sa.Column("cliente_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clientes.id", ondelete="SET NULL")),
        sa.Column("sucursal_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sucursales.id", ondelete="SET NULL")),
        sa.Column("resuelto_via", sa.String(16)),                     # qué sistema resolvió al cliente
        sa.Column("ambiguo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("remision_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("remisiones.id", ondelete="SET NULL")),
        # OC cruda tal como la parseó el bot (incluye las líneas). Es la evidencia:
        # si el cruce se corrige después, aquí sigue el original.
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "origen_externo", name="uq_oc_recibida_origen"),
    )
    op.create_index("ix_oc_recibidas_tenant", "oc_recibidas", ["tenant_id"])
    op.create_index("ix_oc_recibidas_estado", "oc_recibidas", ["tenant_id", "estado"])
    op.create_index("ix_oc_recibidas_cliente", "oc_recibidas", ["cliente_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON oc_recibidas TO app_user")
    op.execute("ALTER TABLE oc_recibidas ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON oc_recibidas "
        "USING (tenant_id = public.current_tenant_id())"
    )

    # ── 3. idempotencia de la remisión ───────────────────────────────────────
    op.add_column("remisiones", sa.Column("origen_externo", sa.String(120)))
    # Índice PARCIAL: las remisiones capturadas a mano no traen ancla y deben
    # poder ser miles con NULL (un UNIQUE normal en Postgres lo permitiría, pero
    # el parcial además no las indexa: más chico y más rápido).
    op.execute(
        "CREATE UNIQUE INDEX uq_remision_origen_externo ON remisiones (tenant_id, origen_externo) "
        "WHERE origen_externo IS NOT NULL"
    )

    # ── 4. el código de sucursal es llave de cruce: que no se duplique ───────
    # PARCIAL sobre las vivas: `sucursales` tiene borrado lógico, y una sucursal
    # dada de baja no puede reservar su código para siempre — de hecho en la BD
    # ya conviven dos SUC-01 del mismo cliente, una de ellas borrada.
    op.execute(
        "CREATE UNIQUE INDEX uq_sucursal_tenant_cliente_codigo "
        "ON sucursales (tenant_id, cliente_id, codigo) WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    # Ambas formas: en su primera versión esto fue una UNIQUE CONSTRAINT, que
    # DROP INDEX no alcanza a borrar.
    op.execute("ALTER TABLE sucursales DROP CONSTRAINT IF EXISTS uq_sucursal_tenant_cliente_codigo")
    op.execute("DROP INDEX IF EXISTS uq_sucursal_tenant_cliente_codigo")
    op.execute("DROP INDEX IF EXISTS uq_remision_origen_externo")
    op.drop_column("remisiones", "origen_externo")

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON oc_recibidas")
    op.execute("ALTER TABLE oc_recibidas DISABLE ROW LEVEL SECURITY")
    op.drop_table("oc_recibidas")

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON cliente_externos")
    op.execute("ALTER TABLE cliente_externos DISABLE ROW LEVEL SECURITY")
    op.drop_table("cliente_externos")
