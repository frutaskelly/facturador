"""Grupos de cobranza: varias razones sociales en un solo estado de cuenta.

Dueño (2-oct-2026): EHMO quiere ver juntas sus tres razones sociales (EHMO,
SUREÑA y MAFAN) en un solo correo, con la tabla por proyecto; otro cliente la
querrá por serie, por sucursal o por razón social. Un grupo dice qué entra
(`cobranza_grupo_alcance`), cómo se acomoda la tabla (`agrupar_por`) y a quién
se manda.

El alcance es por razón social: una fila sin proyecto ni serie = la razón
social completa (sus proyectos nuevos entran solos); filas con proyecto o con
serie (la serie es para lo que no cae en ningún proyecto) = solo eso.

`clientes.nombre_corto` es como se llama la razón social en las tablas
(EHMO en vez de GRUPO OPERADOR DE ALIMENTOS EHMO). Vacío = la razón social.

Entrega 1: los grupos se arman y se previsualizan; la cola semanal sigue con
`cobranza_contactos` hasta la entrega 2.

Revision ID: 0097_grupos_cobranza
Revises: 0096_revision_catalogo
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0097_grupos_cobranza"
down_revision: Union[str, None] = "0096_revision_catalogo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLAS = ("cobranza_grupos", "cobranza_grupo_alcance")


def _base():
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("clientes", sa.Column("nombre_corto", sa.String(40)))

    op.create_table(
        "cobranza_grupos",
        *_base(),
        sa.Column("nombre", sa.String(80), nullable=False),
        sa.Column("agrupar_por", sa.String(10), nullable=False, server_default="PROYECTO"),
        sa.Column("mostrar_antiguedad", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("correos", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("cc", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("pausado", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("motivo_pausa", sa.String(254)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("agrupar_por IN ('PROYECTO', 'SERIE', 'SUCURSAL', 'CLIENTE')",
                           name="ck_cobranza_grupo_agrupar_por"),
    )
    op.create_index("ix_cobranza_grupos_tenant_id", "cobranza_grupos", ["tenant_id"])
    op.execute("CREATE UNIQUE INDEX uq_cobranza_grupo_nombre ON cobranza_grupos (tenant_id, lower(nombre))")

    op.create_table(
        "cobranza_grupo_alcance",
        *_base(),
        sa.Column("grupo_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cobranza_grupos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cliente_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proyecto_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("proyectos.id", ondelete="CASCADE")),
        sa.Column("serie", sa.String(10)),
        sa.CheckConstraint("proyecto_id IS NULL OR serie IS NULL", name="ck_cobranza_alcance_uno"),
    )
    op.create_index("ix_cobranza_grupo_alcance_tenant_id", "cobranza_grupo_alcance", ["tenant_id"])
    op.create_index("ix_cobranza_grupo_alcance_grupo_id", "cobranza_grupo_alcance", ["grupo_id"])
    op.create_index("ix_cobranza_grupo_alcance_cliente_id", "cobranza_grupo_alcance", ["cliente_id"])
    op.create_index("ix_cobranza_grupo_alcance_proyecto_id", "cobranza_grupo_alcance", ["proyecto_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_cobranza_grupo_alcance ON cobranza_grupo_alcance "
        "(grupo_id, cliente_id, COALESCE(proyecto_id::text, ''), COALESCE(serie, ''))"
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
    op.drop_column("clientes", "nombre_corto")
