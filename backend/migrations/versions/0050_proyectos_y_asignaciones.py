"""Listas de precios por cliente, sucursal, serie o proyecto — una sola tabla.

Hasta ahora la lista se colgaba de una columna en cada entidad
(`clientes.lista_precios_id`, `sucursales.lista_precios_id`). Con dos ejes
funcionaba; con cuatro —el negocio también negocia POR SERIE y POR PROYECTO—
significaría cuatro columnas en cuatro tablas y cuatro ramas en el resolutor,
y nadie podría contestar «¿de dónde salió este precio?» sin leer código.

Se cambia por lo que ya usan los ERP para condiciones de venta: un renglón por
NEGOCIACIÓN, con las dimensiones que apliquen llenas y el resto en NULL
(comodín). Gana el renglón que coincide en las dimensiones más específicas:

    proyecto 8 · serie 4 · sucursal 2 · cliente 1

La suma vive en la columna generada `especificidad`: los pesos son UNA sola
declaración, en la base, y el resolutor sólo hace `ORDER BY especificidad DESC`.
Un renglón con las cuatro dimensiones en NULL está prohibido a propósito — ese
es el papel de `listas_precios.es_default`, y dos formas de decir «la lista
base» acabarían discrepando.

También entra `proyectos`, la entidad que faltaba: hasta hoy «HOSPITALES E IMSS
BIENESTAR» sólo existía como texto suelto dentro de la bandeja de OC. Se enlaza
con las equivalencias (`cliente_externos.proyecto_id`) para que la orden que
llega por WhatsApp se etiquete sola, y viaja al documento
(`oc_recibidas` → `remisiones` → `facturas`).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050_proyectos_y_asignaciones"
down_revision: Union[str, None] = "0049_serie_por_grupo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Los pesos de cada dimensión, en un solo lugar. Si el negocio decide que la
# sucursal manda sobre el proyecto, se cambia aquí (y en el modelo) y nada más.
_ESPECIFICIDAD = (
    "(CASE WHEN proyecto_id IS NOT NULL THEN 8 ELSE 0 END) + "
    "(CASE WHEN serie_id    IS NOT NULL THEN 4 ELSE 0 END) + "
    "(CASE WHEN sucursal_id IS NOT NULL THEN 2 ELSE 0 END) + "
    "(CASE WHEN cliente_id  IS NOT NULL THEN 1 ELSE 0 END)"
)

# Sentinelas para la unicidad: en un índice, NULL nunca es igual a NULL, así que
# dos renglones «cliente EHMO, todo lo demás en blanco» pasarían sin chistar.
_NIL = "'00000000-0000-0000-0000-000000000000'::uuid"
_NIL_DATE = "'0001-01-01'::date"


def upgrade() -> None:
    op.create_table(
        "proyectos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("codigo", sa.String(20), nullable=False),
        sa.Column("nombre", sa.String(254), nullable=False),
        # De quién es la negociación. NULL = del grupo (varios clientes pueden
        # comprar bajo el mismo proyecto).
        sa.Column("cliente_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clientes.id", ondelete="CASCADE")),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notas", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "codigo", name="uq_proyecto_tenant_codigo"),
    )
    op.create_index("ix_proyectos_tenant", "proyectos", ["tenant_id"])
    op.create_index("ix_proyectos_cliente", "proyectos", ["cliente_id"])

    op.create_table(
        "lista_asignaciones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lista_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("listas_precios.id", ondelete="CASCADE"), nullable=False),
        # Las cuatro dimensiones. NULL = comodín («aplica a cualquiera»).
        sa.Column("cliente_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clientes.id", ondelete="CASCADE")),
        sa.Column("sucursal_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sucursales.id", ondelete="CASCADE")),
        sa.Column("serie_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("series.id", ondelete="CASCADE")),
        sa.Column("proyecto_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("proyectos.id", ondelete="CASCADE")),
        sa.Column("vigencia_desde", sa.Date()),
        sa.Column("vigencia_hasta", sa.Date()),
        sa.Column("notas", sa.Text()),
        sa.Column("especificidad", sa.SmallInteger(),
                  sa.Computed(_ESPECIFICIDAD, persisted=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "cliente_id IS NOT NULL OR sucursal_id IS NOT NULL "
            "OR serie_id IS NOT NULL OR proyecto_id IS NOT NULL",
            name="ck_asignacion_alguna_dimension",
        ),
    )
    op.create_index("ix_lista_asignaciones_tenant", "lista_asignaciones", ["tenant_id"])
    op.create_index("ix_lista_asignaciones_lista", "lista_asignaciones", ["lista_id"])
    # El índice que sirve al resolutor: entra por tenant y sale ordenado.
    op.execute(
        "CREATE INDEX ix_lista_asignaciones_resolucion ON lista_asignaciones "
        "(tenant_id, especificidad DESC)"
    )
    # Misma combinación de dimensiones + misma fecha de arranque = duplicado.
    # Con `vigencia_desde` dentro, renovar una negociación (mismo alcance, nuevo
    # periodo) sigue siendo legal.
    op.execute(
        f"CREATE UNIQUE INDEX uq_lista_asignacion_dims ON lista_asignaciones ("
        f"tenant_id, "
        f"coalesce(cliente_id,  {_NIL}), "
        f"coalesce(sucursal_id, {_NIL}), "
        f"coalesce(serie_id,    {_NIL}), "
        f"coalesce(proyecto_id, {_NIL}), "
        f"coalesce(vigencia_desde, {_NIL_DATE}))"
    )

    for tabla in ("proyectos", "lista_asignaciones"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tabla} TO app_user")
        op.execute(f"ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {tabla} "
            "USING (tenant_id = public.current_tenant_id())"
        )

    # ── el proyecto viaja: equivalencia → bandeja → remisión → factura ──
    op.add_column("cliente_externos", sa.Column(
        "proyecto_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("proyectos.id", ondelete="SET NULL")))
    for tabla in ("oc_recibidas", "remisiones", "facturas"):
        op.add_column(tabla, sa.Column(
            "proyecto_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("proyectos.id", ondelete="SET NULL")))

    # La serie con la que se emitió, guardada en el documento. Hasta ahora se
    # resolvía, se quemaba el folio y se olvidaba: al reeditar una remisión
    # había que volver a adivinarla. Ahora que la serie DECIDE PRECIOS, olvidarla
    # significaría reprecificar distinto al reeditar.
    op.add_column("remisiones", sa.Column(
        "serie_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("series.id", ondelete="SET NULL")))

    # ── las asignaciones que ya existían, tal cual, sin cambiar un precio ──
    # La sucursal se guarda CON su cliente: el renglón se lee solo en pantalla
    # ("EHMO · Pachuca") y sigue ganándole al del cliente por especificidad.
    op.execute(
        "INSERT INTO lista_asignaciones (tenant_id, lista_id, cliente_id) "
        "SELECT tenant_id, lista_precios_id, id FROM clientes "
        " WHERE lista_precios_id IS NOT NULL AND deleted_at IS NULL"
    )
    op.execute(
        "INSERT INTO lista_asignaciones (tenant_id, lista_id, cliente_id, sucursal_id) "
        "SELECT tenant_id, lista_precios_id, cliente_id, id FROM sucursales "
        " WHERE lista_precios_id IS NOT NULL AND deleted_at IS NULL"
    )
    op.drop_column("clientes", "lista_precios_id")
    op.drop_column("sucursales", "lista_precios_id")


def downgrade() -> None:
    op.add_column("clientes", sa.Column(
        "lista_precios_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("listas_precios.id", ondelete="SET NULL")))
    op.add_column("sucursales", sa.Column(
        "lista_precios_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("listas_precios.id", ondelete="SET NULL")))
    op.execute(
        "UPDATE clientes c SET lista_precios_id = a.lista_id "
        "  FROM lista_asignaciones a "
        " WHERE a.cliente_id = c.id AND a.sucursal_id IS NULL "
        "   AND a.serie_id IS NULL AND a.proyecto_id IS NULL"
    )
    op.execute(
        "UPDATE sucursales s SET lista_precios_id = a.lista_id "
        "  FROM lista_asignaciones a "
        " WHERE a.sucursal_id = s.id AND a.serie_id IS NULL AND a.proyecto_id IS NULL"
    )
    op.drop_column("remisiones", "serie_id")
    for tabla in ("facturas", "remisiones", "oc_recibidas"):
        op.drop_column(tabla, "proyecto_id")
    op.drop_column("cliente_externos", "proyecto_id")
    for tabla in ("lista_asignaciones", "proyectos"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tabla}")
        op.execute(f"ALTER TABLE {tabla} DISABLE ROW LEVEL SECURITY")
    op.drop_table("lista_asignaciones")
    op.drop_table("proyectos")
