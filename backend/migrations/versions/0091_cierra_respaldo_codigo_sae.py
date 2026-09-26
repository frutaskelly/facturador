"""Cierra `respaldo_codigo_sae_20260917`: RLS y fuera `anon`/`authenticated`.

La tabla es el respaldo de `producto_clientes` que se sacó a mano el 17-sep-2026,
antes de mover la clave SAE al producto (42 filas). Nació con un
`CREATE TABLE ... AS` directo en producción, así que nunca pasó por una
migración ni por la BD de pruebas: la invariante de 0054
(`test_toda_tabla_publica_tiene_rls`) no la podía ver. Vive en `public` —el
esquema que PostgREST publica— sin RLS y con los GRANT por omisión de Supabase,
es decir: con la clave pública del frontend cualquiera la leía, la editaba o le
hacía TRUNCATE. El advisor de seguridad la marcó como ERROR el 26-sep-2026.

El dueño eligió conservar el respaldo y cerrarlo, no borrarlo. RLS sin
políticas basta para que nadie sin BYPASSRLS la vea (el backend no la usa);
el REVOKE es la segunda llave, por si algún día alguien le apaga RLS.

Condicional a propósito: la tabla solo existe en producción. En la BD de
pruebas y en dev esta migración no hace nada. Lo mismo con los roles `anon` y
`authenticated`, que son de Supabase y no existen en el Postgres de pruebas.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0091_cierra_respaldo_codigo_sae"
down_revision: Union[str, None] = "0090_venta_leer_lineas"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLA = "public.respaldo_codigo_sae_20260917"


def upgrade() -> None:
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{_TABLA}') IS NULL THEN
                RETURN;
            END IF;
            ALTER TABLE {_TABLA} ENABLE ROW LEVEL SECURITY;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                REVOKE ALL ON {_TABLA} FROM anon;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                REVOKE ALL ON {_TABLA} FROM authenticated;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # Solo se apaga RLS. Los GRANT a `anon`/`authenticated` NO se devuelven:
    # regresarlos sería reabrir el hueco que esta migración cierra.
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{_TABLA}') IS NOT NULL THEN
                ALTER TABLE {_TABLA} DISABLE ROW LEVEL SECURITY;
            END IF;
        END $$;
    """)
