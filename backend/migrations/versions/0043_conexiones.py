"""Conexiones — claves para que un sistema externo escriba en el Facturador.

Smart Supply necesita permiso para dejar órdenes en la bandeja. La alternativa
era poner la contraseña del dueño en un archivo de la Mac: eso le daría al bot
permiso de timbrar, cancelar y borrar todo, y cortarlo obligaría a cambiar la
contraseña de una persona.

Una conexión es una clave propia, con su propio alcance (dejar órdenes y leer
catálogos; nada de CFDI, nada de borrar) que el dueño genera de un clic y revoca
de otro. De la clave solo se guarda su SHA-256 y los últimos 4 caracteres para
poder nombrarla en pantalla — el texto completo se muestra una sola vez y no
vuelve a existir en ningún lado.

Sin vencimiento (decisión del dueño): una clave que caduca sola se cae de
madrugada, cuando nadie está viendo. La pantalla avisa si lleva más de un año
sin rotarse, que es la parte útil sin el riesgo operativo.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_conexiones"
down_revision: Union[str, None] = "0042_bandeja_oc_y_equivalencias"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conexiones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),          # SMART_SUPPLY
        sa.Column("nombre", sa.String(80), nullable=False),
        # SHA-256 de la clave. Nunca el texto: si alguien lee esta tabla no puede
        # usar lo que ve.
        sa.Column("clave_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("clave_pista", sa.String(8), nullable=False),    # «…T09A» en pantalla
        # PENDIENTE (generada, sin usar) | ACTIVA (ya la usó el bot) | REVOCADA
        sa.Column("estado", sa.String(12), nullable=False, server_default="PENDIENTE"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("activada_at", sa.DateTime(timezone=True)),
        sa.Column("ultimo_uso_at", sa.DateTime(timezone=True)),
        sa.Column("revocada_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_conexiones_tenant", "conexiones", ["tenant_id"])
    # Una sola conexión viva por tipo (decisión: «una», no una por grupo).
    # Parcial: las revocadas se conservan como historial y no estorban.
    op.execute(
        "CREATE UNIQUE INDEX uq_conexion_viva_por_tipo ON conexiones (tenant_id, tipo) "
        "WHERE estado <> 'REVOCADA'"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON conexiones TO app_user")
    op.execute("ALTER TABLE conexiones ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON conexiones "
        "USING (tenant_id = public.current_tenant_id())"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON conexiones")
    op.execute("ALTER TABLE conexiones DISABLE ROW LEVEL SECURITY")
    op.drop_table("conexiones")
