"""Catálogo SAT en la base + presentación por cliente + lista default.

1. Tablas GLOBALES (no tenant) `sat_clave_prodserv` (52,513 claves) y
   `sat_clave_unidad` (2,417 unidades), sembradas desde los csv.gz generados
   del catálogo oficial catCFDI_V_4 (2026-08-06). Son la ÚNICA fuente para
   sugerir y validar claves SAT: la IA propone, pero solo se aceptan claves
   que existen aquí. Índice FTS en español para la búsqueda por texto.
2. `producto_clientes.presentacion`: la unidad con la que ESE cliente compra
   el producto (Cilantro por MANOJO vs KILO — un solo producto).
3. `listas_precios.es_default`: la lista base del negocio, asignable desde el
   wizard de importación (antes solo la convención codigo='UNICO').
"""
import csv
import gzip
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041_catalogo_sat_y_listas"
down_revision: Union[str, None] = "0040_producto_clientes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DATA = os.path.join(os.path.dirname(__file__), "..", "..", "data")

# Expresión del índice FTS — la MISMA que usa la búsqueda en el servicio.
_TS = "to_tsvector('spanish', coalesce(descripcion,'') || ' ' || coalesce(palabras_similares,''))"


def _cargar(nombre_archivo: str, tabla, campos: list[str]) -> None:
    ruta = os.path.join(_DATA, nombre_archivo)
    with gzip.open(ruta, "rt", encoding="utf-8") as fh:
        filas = [dict(zip(campos, row)) for row in csv.reader(fh)][1:]  # sin encabezado
    bind = op.get_bind()
    for i in range(0, len(filas), 5000):
        bind.execute(tabla.insert(), filas[i:i + 5000])


def upgrade() -> None:
    prodserv = op.create_table(
        "sat_clave_prodserv",
        sa.Column("clave", sa.String(8), primary_key=True),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("palabras_similares", sa.Text()),
    )
    unidad = op.create_table(
        "sat_clave_unidad",
        sa.Column("clave", sa.String(20), primary_key=True),
        sa.Column("nombre", sa.Text(), nullable=False),
    )
    _cargar("sat_clave_prodserv.csv.gz", prodserv, ["clave", "descripcion", "palabras_similares"])
    _cargar("sat_clave_unidad.csv.gz", unidad, ["clave", "nombre"])
    op.execute(f"CREATE INDEX ix_sat_prodserv_fts ON sat_clave_prodserv USING GIN ({_TS})")
    # Catálogo global de solo lectura para la app (sin RLS: no hay tenant).
    op.execute("GRANT SELECT ON sat_clave_prodserv, sat_clave_unidad TO app_user")

    op.add_column("producto_clientes", sa.Column("presentacion", sa.String(20)))
    op.add_column(
        "listas_precios",
        sa.Column("es_default", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("listas_precios", "es_default")
    op.drop_column("producto_clientes", "presentacion")
    op.drop_table("sat_clave_unidad")
    op.drop_table("sat_clave_prodserv")
