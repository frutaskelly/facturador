"""Cobranza automática: configuración, contactos y la cola/bitácora de envíos.

El estado de cuenta que hoy se manda a mano (POST /cobranza/estado-cuenta/{id}/
enviar) sale cada semana: el reloj arma la cola, alguien la aprueba (o sale
sola en modo AUTOMATICO) y cada envío queda registrado.

El índice único de `cobranza_envios` es el candado contra el doble envío: por
contacto (cliente + serie) y fecha de corte hay UNA fila, se genere desde el
reloj o desde el botón.

Revision ID: 0088_cobranza_automatica
Revises: 0087_fecha_entrega_oc_recibida
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0088_cobranza_automatica"
down_revision: Union[str, None] = "0087_fecha_entrega_oc_recibida"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLAS = ("cobranza_config", "cobranza_contactos", "cobranza_envios")


def _base():
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
    ]


def _tiempos():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def _lista():
    return dict(nullable=False, server_default=sa.text("'[]'::jsonb"))


def upgrade() -> None:
    op.create_table(
        "cobranza_config",
        *_base(),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("modo", sa.String(12), nullable=False, server_default="REVISION"),
        sa.Column("dia_semana", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("hora", sa.Integer(), nullable=False, server_default=sa.text("8")),
        sa.Column("zona", sa.String(40), nullable=False, server_default="America/Mexico_City"),
        sa.Column("incluir_por_vencer", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("saldo_minimo", sa.Numeric(18, 2), nullable=False, server_default=sa.text("100")),
        sa.Column("escalar_dias", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column("escalar_cc", postgresql.JSONB(), **_lista()),
        sa.Column("cc_siempre", postgresql.JSONB(), **_lista()),
        sa.Column("adjuntar_pdf", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("adjuntar_excel", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("espejo_max_horas", sa.Integer(), nullable=False, server_default=sa.text("6")),
        sa.Column("asunto", sa.String(200)),
        sa.Column("mensaje", sa.Text()),
        sa.Column("ultima_generacion", sa.Date()),
        *_tiempos(),
        sa.CheckConstraint("modo IN ('REVISION', 'AUTOMATICO')", name="ck_cobranza_config_modo"),
        sa.CheckConstraint("dia_semana BETWEEN 0 AND 6", name="ck_cobranza_config_dia"),
        sa.CheckConstraint("hora BETWEEN 0 AND 23", name="ck_cobranza_config_hora"),
        sa.UniqueConstraint("tenant_id", name="uq_cobranza_config_tenant"),
    )

    op.create_table(
        "cobranza_contactos",
        *_base(),
        sa.Column("cliente_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("serie", sa.String(10)),
        sa.Column("correos", postgresql.JSONB(), **_lista()),
        sa.Column("cc", postgresql.JSONB(), **_lista()),
        sa.Column("pausado", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("motivo_pausa", sa.String(254)),
        *_tiempos(),
    )
    op.create_index("ix_cobranza_contactos_tenant_id", "cobranza_contactos", ["tenant_id"])
    op.create_index("ix_cobranza_contactos_cliente_id", "cobranza_contactos", ["cliente_id"])
    # Un contacto por cliente y serie; NULL (todas las series) cuenta como uno más.
    op.execute(
        "CREATE UNIQUE INDEX uq_cobranza_contacto ON cobranza_contactos "
        "(tenant_id, cliente_id, COALESCE(serie, ''))"
    )

    op.create_table(
        "cobranza_envios",
        *_base(),
        sa.Column("cliente_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("serie", sa.String(10)),
        sa.Column("corte", sa.Date(), nullable=False),
        sa.Column("estado", sa.String(12), nullable=False, server_default="PENDIENTE"),
        sa.Column("origen", sa.String(12), nullable=False, server_default="PROGRAMADO"),
        sa.Column("para", postgresql.JSONB(), **_lista()),
        sa.Column("cc", postgresql.JSONB(), **_lista()),
        sa.Column("saldo", sa.Numeric(18, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("vencido", sa.Numeric(18, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("facturas", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("dias_max_vencida", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("escalado", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("error", sa.Text()),
        sa.Column("aprobado_por", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("enviado_at", sa.DateTime(timezone=True)),
        *_tiempos(),
        sa.CheckConstraint(
            "estado IN ('PENDIENTE', 'ENVIANDO', 'ENVIADO', 'ERROR', 'DESCARTADO')",
            name="ck_cobranza_envio_estado"),
    )
    op.create_index("ix_cobranza_envios_tenant_id", "cobranza_envios", ["tenant_id"])
    op.create_index("ix_cobranza_envios_cliente_id", "cobranza_envios", ["cliente_id"])
    op.create_index("ix_cobranza_envios_estado", "cobranza_envios", ["tenant_id", "estado"])
    op.execute(
        "CREATE UNIQUE INDEX uq_cobranza_envio ON cobranza_envios "
        "(tenant_id, cliente_id, COALESCE(serie, ''), corte)"
    )

    for t in _TABLAS:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO app_user")
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            "USING (tenant_id = public.current_tenant_id())"
        )


def downgrade() -> None:
    for t in reversed(_TABLAS):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.drop_table(t)
