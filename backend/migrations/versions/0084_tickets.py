"""Buzón de tickets: los casos que el bot no resolvió solo, con su número.

Caso real (23-sep-2026): un «necesito una mano con este pedido» del bot se
perdió en el chat. Cada aviso trae ahora un número (lo pone el bot) y aterriza
aquí, donde se puede revisar la foto y resolverlo sin volver a WhatsApp.

Permisos nuevos, sembrados sin quitarle nada a nadie:
  * `menu:tickets`     — ver el buzón. Lo reciben los roles que ven remisiones:
                          los tickets de hoy son pedidos atorados.
  * `ticket:gestionar` — resolverlos. Lo reciben los que gestionan remisiones.
La conexión del bot los trae en PERMISOS_CONEXION (código, no catálogo).

Revision ID: 0084_tickets
Revises: 0083_linea_factura_clave_sae
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0084_tickets"
down_revision: Union[str, None] = "0083_linea_factura_clave_sae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("numero", sa.Integer(), nullable=False),
        sa.Column("canal", sa.String(20), nullable=False, server_default="WHATSAPP"),
        sa.Column("origen_externo", sa.String(160), nullable=False),
        sa.Column("estado", sa.String(10), nullable=False, server_default="ABIERTO"),
        sa.Column("perfil", sa.String(40)),
        sa.Column("grupo", sa.String(160)),
        sa.Column("jid", sa.String(120)),
        sa.Column("remitente", sa.String(160)),
        sa.Column("archivo_nombre", sa.String(254)),
        sa.Column("nota", sa.Text()),
        sa.Column("tipo", sa.String(40)),
        sa.Column("que_paso", sa.Text()),
        sa.Column("acciones", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("foto", sa.LargeBinary()),
        sa.Column("foto_mime", sa.String(40)),
        sa.Column("accion_pedida", sa.String(10)),
        sa.Column("accion_pedida_por", sa.String(160)),
        sa.Column("accion_pedida_at", sa.DateTime(timezone=True)),
        sa.Column("accion_tomada_at", sa.DateTime(timezone=True)),
        sa.Column("resolucion", sa.Text()),
        sa.Column("resuelto_at", sa.DateTime(timezone=True)),
        sa.Column("resuelto_por", sa.String(160)),
        sa.Column("eventos", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("recibido_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "numero", name="uq_ticket_numero"),
        sa.UniqueConstraint("tenant_id", "origen_externo", name="uq_ticket_origen"),
    )
    op.create_index("ix_tickets_tenant_id", "tickets", ["tenant_id"])
    op.create_index("ix_tickets_estado", "tickets", ["tenant_id", "estado"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON tickets TO app_user")
    op.execute("ALTER TABLE tickets ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON tickets "
        "USING (tenant_id = public.current_tenant_id())"
    )

    for pid, recurso, accion, desc in (
        ("menu:tickets", "menu", "tickets", "Buzón de tickets"),
        ("ticket:gestionar", "ticket", "gestionar", "Resolver tickets del bot (sumar como complemento, cerrar)"),
    ):
        op.get_bind().exec_driver_sql(
            "INSERT INTO permissions (id, recurso, accion, vertical, descripcion) "
            f"VALUES ('{pid}', '{recurso}', '{accion}', NULL, '{desc}') "
            "ON CONFLICT (id) DO NOTHING"
        )
    for nuevo, base in (("menu:tickets", "menu:remisiones"), ("ticket:gestionar", "remision:gestionar")):
        op.get_bind().exec_driver_sql(
            "INSERT INTO role_permissions (role_id, permission_id) "
            f"SELECT rp.role_id, '{nuevo}' FROM role_permissions rp "
            f"WHERE rp.permission_id = '{base}' "
            # el portal de cliente ve SUS remisiones, no los tickets internos
            "AND rp.role_id NOT IN (SELECT id FROM roles WHERE nombre = 'PORTAL CLIENTE' AND es_preset) "
            "ON CONFLICT DO NOTHING"
        )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DELETE FROM role_permissions WHERE permission_id IN ('menu:tickets', 'ticket:gestionar')"
    )
    op.get_bind().exec_driver_sql(
        "DELETE FROM permissions WHERE id IN ('menu:tickets', 'ticket:gestionar')"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON tickets")
    op.drop_index("ix_tickets_estado", table_name="tickets")
    op.drop_index("ix_tickets_tenant_id", table_name="tickets")
    op.drop_table("tickets")
