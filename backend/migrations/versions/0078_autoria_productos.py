"""Quién dio de alta este producto — y a qué clientes se lo escribió.

El 16-sep-2026 fue imposible saber quién creó "CHAYOTE SIN ESPINA" y sus cinco
filas de catálogo apuntando a clientes equivocados: `productos` y
`producto_clientes` no guardaban autor, no hay tabla de auditoría, el
`audit_log_entries` de Supabase está vacío (el auth es propio) y el Excel de la
importación no se conserva. El rastro existía solo en la cabeza de quien lo
hizo.

Dos piezas, ninguna retroactiva (lo ya escrito se queda sin autor: inventarlo
sería peor que el hueco):

1. `created_by`/`updated_by` en ambas tablas, igual que en remisiones. Nullable
   y `SET NULL` a propósito: el seed y los scripts crean productos sin usuario,
   y una conexión del bot no tiene persona a la que atribuir.
2. `import_productos_log`: un renglón por pasada de la importación masiva —
   quién, cuándo, con qué archivo, para qué clientes y cuántas filas tocó. Las
   columnas de autor dicen quién creó UNA fila; la bitácora dice de qué lote
   salió, que es la pregunta que se hizo hoy.

Revision ID: 0078_autoria_productos
Revises: 0079_clave_sae_producto

OJO CON EL NÚMERO: esta nació colgando de la 0076 y se quedó abierta dos días,
en los que `main` encadenó 0078_remision_impresa y 0079_clave_sae_producto sobre
esa misma 0076. Dejarla donde nació le daba a alembic DOS cabezas y `upgrade
head` truena — git no lo ve porque no hay choque de texto. Por eso corre DESPUÉS
de la 0079 aunque se llame 0078: alembic va por el grafo, no por el nombre, y
renombrar la revisión rompería cualquier base que ya la tuviera estampada.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0078_autoria_productos"
down_revision: Union[str, None] = "0079_clave_sae_producto"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLAS = ("productos", "producto_clientes")


def upgrade() -> None:
    for tabla in _TABLAS:
        for col in ("created_by", "updated_by"):
            op.add_column(
                tabla,
                sa.Column(col, postgresql.UUID(as_uuid=True), nullable=True),
            )
            op.create_foreign_key(
                f"fk_{tabla}_{col}", tabla, "users", [col], ["id"], ondelete="SET NULL",
            )
    # Sin índice sobre created_by/updated_by: la consulta es forense ("¿quién
    # creó esto?"), siempre por id o por producto, nunca un barrido por autor.
    # Un índice de más aquí es peso muerto en cada alta masiva.

    op.create_table(
        "import_productos_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        # IMPORT   = POST /productos/importar (el alta masiva)
        # CATALOGO = POST /productos/catalogo-cliente-batch (el último paso del
        #            wizard, que es donde se eligen los clientes de verdad)
        sa.Column("origen", sa.String(12), nullable=False, server_default="IMPORT"),
        # NULL si quien importó fue una conexión y no una persona.
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        # El archivo no se guarda; su NOMBRE sí, que es con lo que el usuario
        # lo reconoce ("LISTA EHMO SEP.xlsx") y lo puede volver a abrir.
        sa.Column("archivo_nombre", sa.String(254)),
        # Los clientes elegidos, como lista de ids: es la pregunta del incidente
        # ("¿a quién le escribió el catálogo?") y son pocos por pasada.
        sa.Column(
            "cliente_ids", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default="[]",
        ),
        sa.Column("filas_enviadas", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("productos_creados", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("productos_vinculados", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Filas de producto_clientes escritas (nuevas + pisadas).
        sa.Column("catalogo_guardado", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("precios_guardados", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("filas_con_error", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # El resto del resumen (omitidos, categorías creadas, lista destino…):
        # cambia con el wizard y no merece una columna cada vez.
        sa.Column("detalle", postgresql.JSONB(astext_type=sa.Text())),
    )
    # La única consulta es "las últimas pasadas de este tenant".
    op.create_index(
        "ix_import_productos_log_tenant",
        "import_productos_log",
        ["tenant_id", "created_at"],
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON import_productos_log TO app_user")
    op.execute("ALTER TABLE import_productos_log ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON import_productos_log "
        "USING (tenant_id = public.current_tenant_id())"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON import_productos_log")
    op.execute("ALTER TABLE import_productos_log DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_import_productos_log_tenant", table_name="import_productos_log")
    op.drop_table("import_productos_log")
    for tabla in _TABLAS:
        for col in ("updated_by", "created_by"):
            op.drop_constraint(f"fk_{tabla}_{col}", tabla, type_="foreignkey")
            op.drop_column(tabla, col)
