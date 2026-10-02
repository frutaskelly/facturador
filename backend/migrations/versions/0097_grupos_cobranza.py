"""Envíos de cobranza: cada estado de cuenta se configura por separado.

Dueño (2-oct-2026): EHMO quiere ver juntas sus tres razones sociales (EHMO,
SUREÑA y MAFAN) en un solo correo, con la tabla por proyecto; otro cliente la
querrá por serie, por sucursal o por razón social. Y cada cobranza se
configura sola: a quién, cuándo, automática o a mano, qué incluye, el correo.

- `cobranza_grupos`: un ENVÍO (una razón social o varias). Trae todo lo que
  antes estaba en Contactos y en Ajustes: correos, modo (AUTOMATICO = sale
  sola el día y la hora que diga; MANUAL = solo con el botón Enviar),
  saldo mínimo, escalamiento, adjuntos, asunto y mensaje.
- `cobranza_grupo_alcance`: qué entra, por razón social. Sin proyecto ni serie
  = la razón social completa (sus proyectos nuevos entran solos); con proyecto
  o serie (para lo que no cae en ningún proyecto) = solo eso.
- `cobranza_envios` queda como la bitácora: cada correo que salió, con su envío.
  Un programado por envío y día (índice parcial): el reloj no manda dos.
- `cobranza_config` se queda con lo general: el interruptor maestro de los
  automáticos, el candado del espejo y la copia fija a todos.
- `clientes.nombre_corto`: como se llama la razón social en las tablas
  (EHMO en vez de GRUPO OPERADOR DE ALIMENTOS EHMO). Vacío = la razón social.

Los contactos capturados se vuelven envíos en modo MANUAL (nada sale solo
por la migración) y la cola vieja que no salió se descarta con su motivo.
`cobranza_contactos` se queda como respaldo; ya nada la lee.

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
_LISTA = dict(nullable=False, server_default=sa.text("'[]'::jsonb"))


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
        sa.Column("correos", postgresql.JSONB(), **_LISTA),
        sa.Column("cc", postgresql.JSONB(), **_LISTA),
        sa.Column("modo", sa.String(12), nullable=False, server_default="MANUAL"),
        sa.Column("dia_semana", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("hora", sa.Integer(), nullable=False, server_default=sa.text("8")),
        sa.Column("incluir_por_vencer", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("saldo_minimo", sa.Numeric(18, 2), nullable=False, server_default=sa.text("100")),
        sa.Column("escalar_dias", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column("escalar_cc", postgresql.JSONB(), **_LISTA),
        sa.Column("adjuntar_pdf", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("adjuntar_excel", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("asunto", sa.String(200)),
        sa.Column("mensaje", sa.Text()),
        sa.Column("nota", sa.String(254)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("agrupar_por IN ('PROYECTO', 'SERIE', 'SUCURSAL', 'CLIENTE')",
                           name="ck_cobranza_grupo_agrupar_por"),
        sa.CheckConstraint("modo IN ('AUTOMATICO', 'MANUAL')", name="ck_cobranza_grupo_modo"),
        sa.CheckConstraint("dia_semana BETWEEN 0 AND 6", name="ck_cobranza_grupo_dia"),
        sa.CheckConstraint("hora BETWEEN 0 AND 23", name="ck_cobranza_grupo_hora"),
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

    # La bitácora: cada correo apunta a su envío (y guarda el nombre por si el
    # envío se borra). Los del envío ya no son de UN cliente.
    op.add_column("cobranza_envios", sa.Column(
        "grupo_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("cobranza_grupos.id", ondelete="SET NULL")))
    op.add_column("cobranza_envios", sa.Column("grupo_nombre", sa.String(80)))
    op.create_index("ix_cobranza_envios_grupo_id", "cobranza_envios", ["grupo_id"])
    op.alter_column("cobranza_envios", "cliente_id", nullable=True)
    op.execute("DROP INDEX IF EXISTS uq_cobranza_envio")
    op.execute(
        "CREATE UNIQUE INDEX uq_cobranza_envio_programado ON cobranza_envios (grupo_id, corte) "
        "WHERE origen = 'PROGRAMADO' AND grupo_id IS NOT NULL"
    )

    _contactos_a_envios()
    op.execute(
        "UPDATE cobranza_envios SET estado = 'DESCARTADO', "
        "error = 'La cola por contacto se reemplazó por los envíos (oct-2026).' "
        "WHERE grupo_id IS NULL AND estado IN ('PENDIENTE', 'ENVIANDO', 'ERROR')"
    )


def _contactos_a_envios() -> None:
    """Cada contacto capturado se vuelve un envío MANUAL con los ajustes que
    tenía el inquilino. Un contacto por serie entra con los proyectos de esa
    serie y la serie misma (lo que no cae en ningún proyecto)."""
    conn = op.get_bind()
    cfgs = {r.tenant_id: r for r in conn.execute(sa.text("SELECT * FROM cobranza_config"))}
    filas = conn.execute(sa.text(
        "SELECT cc.*, c.legal_name FROM cobranza_contactos cc "
        "JOIN clientes c ON c.id = cc.cliente_id WHERE c.deleted_at IS NULL "
        "ORDER BY c.legal_name, cc.serie NULLS FIRST")).fetchall()
    usados: dict = {}
    for f in filas:
        base = (f.legal_name or "Cliente")[:68] + (f" · {f.serie}" if f.serie else "")
        nombre, n = base, 2
        tomados = usados.setdefault(f.tenant_id, set())
        while nombre.lower() in tomados:
            nombre = f"{base} ({n})"
            n += 1
        tomados.add(nombre.lower())
        cfg = cfgs.get(f.tenant_id)
        valores = dict(
            tenant_id=f.tenant_id, nombre=nombre,
            dia_semana=cfg.dia_semana if cfg else 0, hora=cfg.hora if cfg else 8,
            incluir_por_vencer=cfg.incluir_por_vencer if cfg else True,
            saldo_minimo=cfg.saldo_minimo if cfg else 100,
            escalar_dias=cfg.escalar_dias if cfg else 30,
            adjuntar_pdf=cfg.adjuntar_pdf if cfg else True,
            adjuntar_excel=cfg.adjuntar_excel if cfg else True,
            asunto=cfg.asunto if cfg else None, mensaje=cfg.mensaje if cfg else None,
            nota=(f"En pausa: {f.motivo_pausa}" if f.motivo_pausa else "En pausa") if f.pausado else None,
        )
        grupo_id = conn.execute(sa.text(
            "INSERT INTO cobranza_grupos (tenant_id, nombre, modo, correos, cc, dia_semana, hora, "
            "incluir_por_vencer, saldo_minimo, escalar_dias, escalar_cc, adjuntar_pdf, adjuntar_excel, "
            "asunto, mensaje, nota) VALUES (:tenant_id, :nombre, 'MANUAL', "
            "CAST(:correos AS jsonb), CAST(:cc AS jsonb), :dia_semana, :hora, :incluir_por_vencer, "
            ":saldo_minimo, :escalar_dias, CAST(:escalar_cc AS jsonb), :adjuntar_pdf, :adjuntar_excel, "
            ":asunto, :mensaje, :nota) RETURNING id"),
            {**valores, "correos": _json(f.correos), "cc": _json(f.cc),
             "escalar_cc": _json(cfg.escalar_cc if cfg else [])}).scalar()
        if not f.serie:
            conn.execute(sa.text(
                "INSERT INTO cobranza_grupo_alcance (tenant_id, grupo_id, cliente_id) VALUES (:t, :g, :c)"),
                {"t": f.tenant_id, "g": grupo_id, "c": f.cliente_id})
            continue
        conn.execute(sa.text(
            "INSERT INTO cobranza_grupo_alcance (tenant_id, grupo_id, cliente_id, proyecto_id) "
            "SELECT :t, :g, :c, p.id FROM proyectos p WHERE p.tenant_id = :t AND p.cliente_id = :c "
            "AND p.deleted_at IS NULL AND p.series ? :s"),
            {"t": f.tenant_id, "g": grupo_id, "c": f.cliente_id, "s": f.serie})
        conn.execute(sa.text(
            "INSERT INTO cobranza_grupo_alcance (tenant_id, grupo_id, cliente_id, serie) VALUES (:t, :g, :c, :s)"),
            {"t": f.tenant_id, "g": grupo_id, "c": f.cliente_id, "s": f.serie})


def _json(v) -> str:
    import json
    return json.dumps(list(v or []))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_cobranza_envio_programado")
    op.execute("DELETE FROM cobranza_envios WHERE cliente_id IS NULL")
    op.alter_column("cobranza_envios", "cliente_id", nullable=False)
    op.drop_index("ix_cobranza_envios_grupo_id", table_name="cobranza_envios")
    op.drop_column("cobranza_envios", "grupo_nombre")
    op.drop_column("cobranza_envios", "grupo_id")
    op.execute(
        "CREATE UNIQUE INDEX uq_cobranza_envio ON cobranza_envios "
        "(tenant_id, cliente_id, COALESCE(serie, ''), corte)"
    )
    for t in reversed(_TABLAS):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.drop_table(t)
    op.drop_column("clientes", "nombre_corto")
