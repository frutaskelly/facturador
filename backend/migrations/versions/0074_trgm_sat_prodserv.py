"""Índices trigram para el fallback ILIKE del sugeridor de claves SAT.

`buscar_claves_batch` remata con `descripcion ILIKE '%tk%' OR
palabras_similares ILIKE '%tk%'` por cada token que el FTS no llenó. Sin
índice, cada token es un seq scan sobre las 52k filas de
`sat_clave_prodserv`: en producción la variante de 374 tokens promedia
36 s por llamada (pg_stat_statements, ventana 21-ago→11-sep). El FTS ya
tiene su GIN (`ix_sat_prodserv_fts`); el ILIKE no tenía nada.

Un GIN con `gin_trgm_ops` atiende LIKE/ILIKE con comodín inicial sin
cambiar una línea de la query. Dos índices separados (no uno concatenado)
porque el OR se descompone en dos BitmapOr sobre columnas distintas.

Revision ID: 0074_trgm_sat_prodserv
Revises: 0073_proyecto_correos_facturas
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0074_trgm_sat_prodserv"
down_revision: Union[str, None] = "0073_proyecto_correos_facturas"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_sat_prodserv_desc_trgm ON sat_clave_prodserv "
        "USING gin (descripcion gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_sat_prodserv_similares_trgm ON sat_clave_prodserv "
        "USING gin (palabras_similares gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sat_prodserv_similares_trgm")
    op.execute("DROP INDEX IF EXISTS ix_sat_prodserv_desc_trgm")
    # La extensión se queda: otra cosa puede haberse colgado de ella.
