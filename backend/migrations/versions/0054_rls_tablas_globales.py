"""RLS en las cuatro tablas globales que se habían quedado fuera.

El advisor de seguridad de Supabase las marca porque viven en `public` —el
esquema que PostgREST publica— sin Row Level Security. Con los GRANT que
Supabase pone por omisión sobre `anon` y `authenticated`, cualquiera con la
clave pública que el frontend lleva en el bundle podía INSERT/UPDATE/DELETE
sobre los catálogos del SAT. Las 41 tablas restantes ya estaban cubiertas por
su `tenant_isolation`; estas cuatro no son de ningún tenant y se quedaron sin
nada.

No tener tenant no las deja iguales entre sí:

  · `alembic_version` la toca solo alembic, que entra con `DATABASE_URL` como
    `postgres` (BYPASSRLS). RLS sin políticas basta: nadie más la lee ni la
    escribe. OJO al futuro: si algún día se corrieran migraciones con un rol
    sin BYPASSRLS, alembic vería la tabla vacía y querría re-aplicar todo.

  · `permissions`, `sat_clave_prodserv` y `sat_clave_unidad` SÍ las lee el
    backend dentro de las sesiones de `get_tenant_db`, que bajan a `app_user`
    con `SET LOCAL ROLE` — y `app_user` no tiene BYPASSRLS. Sin una política de
    lectura, la pantalla de roles saldría vacía, guardar un rol respondería
    «Permisos desconocidos» y el autocompletar de claves SAT dejaría de
    encontrar nada. De ahí `FOR SELECT TO app_user`: el backend lee, nadie
    escribe, y `anon` sigue sin poder hacer nada.

Numerada 0054 y no 0053 porque `0053_alias_por_cliente` ya existe en la rama
`claude/catalogo-productos-multicliente-c04900`, y `down_revision` cuelga de
ella: mientras las dos apuntaban a 0052, alembic veía DOS cabezas y
`alembic upgrade head` —el paso 2/3 de deploy.sh— salía con error 255.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0054_rls_tablas_globales"
down_revision: Union[str, None] = "0053_alias_por_cliente"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Catálogos globales que el backend lee como `app_user`.
_LECTURA = ("permissions", "sat_clave_prodserv", "sat_clave_unidad")
# Bitácora de alembic: nadie que no sea alembic tiene por qué verla.
_CERRADAS = ("alembic_version",)
_POLITICA = "catalogo_global_lectura"


def upgrade() -> None:
    for tabla in _CERRADAS + _LECTURA:
        op.execute(f"ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY")
    for tabla in _LECTURA:
        # El `DROP ... IF EXISTS` no es adorno: el DDL ya se aplicó a mano en
        # producción el 2026-08-28 para cerrar el hallazgo del advisor sin
        # esperar a un deploy, así que esta migración tiene que poder correr
        # sobre una base que ya lo trae.
        op.execute(f"DROP POLICY IF EXISTS {_POLITICA} ON {tabla}")
        op.execute(
            f"CREATE POLICY {_POLITICA} ON {tabla} FOR SELECT TO app_user USING (true)"
        )


def downgrade() -> None:
    for tabla in _LECTURA:
        op.execute(f"DROP POLICY IF EXISTS {_POLITICA} ON {tabla}")
    for tabla in _CERRADAS + _LECTURA:
        op.execute(f"ALTER TABLE {tabla} DISABLE ROW LEVEL SECURITY")
