"""«Sin categorizar»: la categoría por defecto del sistema, y los huecos que ya existían.

Un producto sin categoría no era un producto en una categoría vacía: era un
hueco invisible. No se podía listar, contar ni repartir desde la pantalla de
Categorías, y así se acumularon (41 al escribir esto, repartidos en dos
empresas) sin que nadie los viera.

A partir de aquí la categoría existe de verdad en cada empresa y el alta la usa
cuando no se elige otra. Esta migración le da el mismo trato a lo que ya estaba:
crea la categoría donde falte y recoge en ella los productos que quedaron sin
ninguna. Es un reacomodo de catálogo, no toca precios ni movimientos.

El código de la categoría NO se escribe a mano: se deriva del nombre igual que
en `services/categoria_codigo.py` (primeros 5 caracteres en mayúsculas, sin
acentos → «SINCA») y, si ese ya estuviera ocupado en esa empresa, se le pone
sufijo numérico hasta encontrar uno libre.

Revision ID: 0059_categoria_sin_categorizar
Revises: 0058_proyecto_sucursales
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0059_categoria_sin_categorizar"
down_revision: Union[str, None] = "0058_proyecto_sucursales"
branch_labels = None
depends_on = None

NOMBRE = "Sin categorizar"
DESCRIPCION = "Productos que aún no se clasifican. Es la categoría por defecto del sistema."


def upgrade() -> None:
    conn = op.get_bind()
    tenants = [r[0] for r in conn.execute(sa.text("SELECT id FROM tenants")).fetchall()]

    for tid in tenants:
        cat_id = conn.execute(
            sa.text(
                "SELECT id FROM categorias_producto "
                "WHERE tenant_id = :t AND nombre = :n AND deleted_at IS NULL"
            ),
            {"t": tid, "n": NOMBRE},
        ).scalar()

        if cat_id is None:
            # Mismo criterio que categoria_codigo.slugify_codigo: «Sin
            # categorizar» → «SINCA». El sufijo cubre el choque improbable.
            ocupados = {
                r[0] for r in conn.execute(
                    sa.text("SELECT codigo FROM categorias_producto WHERE tenant_id = :t"),
                    {"t": tid},
                ).fetchall()
            }
            codigo = "SINCA"
            n = 2
            while codigo in ocupados:
                codigo = f"SINC{n}" if n < 10 else f"SIN{n}"
                n += 1
            cat_id = conn.execute(
                sa.text(
                    "INSERT INTO categorias_producto "
                    "  (id, tenant_id, codigo, nombre, descripcion, activo, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :t, :c, :n, :d, true, now(), now()) "
                    "RETURNING id"
                ),
                {"t": tid, "c": codigo, "n": NOMBRE, "d": DESCRIPCION},
            ).scalar()

        # Los que ya estaban en el hueco se recogen aquí.
        conn.execute(
            sa.text(
                "UPDATE productos SET categoria_id = :c, updated_at = now() "
                "WHERE tenant_id = :t AND categoria_id IS NULL AND deleted_at IS NULL"
            ),
            {"c": cat_id, "t": tid},
        )


def downgrade() -> None:
    # Devuelve al hueco lo que esta migración recogió y quita la categoría.
    # No distingue lo que el usuario haya clasificado a mano aquí después: al
    # revertir, «Sin categorizar» deja de existir y sus productos vuelven a
    # quedarse sin ninguna, que es el estado previo.
    conn = op.get_bind()
    ids = [
        r[0] for r in conn.execute(
            sa.text("SELECT id FROM categorias_producto WHERE nombre = :n"), {"n": NOMBRE}
        ).fetchall()
    ]
    for cid in ids:
        conn.execute(
            sa.text("UPDATE productos SET categoria_id = NULL WHERE categoria_id = :c"),
            {"c": cid},
        )
    conn.execute(sa.text("DELETE FROM categorias_producto WHERE nombre = :n"), {"n": NOMBRE})
