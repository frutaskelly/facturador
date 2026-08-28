"""Ninguna tabla de `public` puede quedarse sin RLS.

Es la invariante que el advisor de Supabase vigila: `public` es el esquema que
PostgREST publica, y ahí los GRANT por omisión de `anon`/`authenticated` dejan
escribir a cualquiera que tenga la clave pública del frontend. Cuatro tablas se
habían quedado fuera (0053); esta prueba impide que vuelva a pasar con la
siguiente tabla que alguien cree.

La segunda mitad comprueba lo otro que importa: que cerrarlas no dejó ciego al
backend, que lee esos catálogos globales bajando a `app_user`.
"""
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

_CATALOGOS_GLOBALES = ("permissions", "sat_clave_prodserv", "sat_clave_unidad")


def test_toda_tabla_publica_tiene_rls(db_engine):
    with db_engine.connect() as conn:
        sin_rls = [
            r[0]
            for r in conn.execute(
                text(
                    """
                    SELECT c.relname
                      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = 'public'
                       AND c.relkind IN ('r', 'p')
                       AND NOT c.relrowsecurity
                     ORDER BY c.relname
                    """
                )
            )
        ]
    assert sin_rls == [], f"tablas públicas sin RLS: {', '.join(sin_rls)}"


def test_app_user_sigue_leyendo_los_catalogos_globales(db_engine):
    """Sin política de lectura, la pantalla de roles y el autocompletar SAT se
    quedarían vacíos en silencio."""
    with db_engine.connect() as conn:
        trans = conn.begin()
        conn.execute(text("SET LOCAL ROLE app_user"))
        # Sin GUC de tenant: estos catálogos no son de nadie, se leen igual.
        for tabla in _CATALOGOS_GLOBALES:
            n = conn.execute(text(f"SELECT count(*) FROM {tabla}")).scalar()
            assert n > 0, f"{tabla} quedó ilegible para app_user"
        trans.rollback()


def test_app_user_no_puede_escribir_en_los_catalogos_globales(db_engine):
    """Da igual por dónde se cierre —RLS en `permissions`, la falta de GRANT en
    los catálogos SAT—: lo que no puede es borrar una fila."""
    with db_engine.connect() as conn:
        trans = conn.begin()
        conn.execute(text("SET LOCAL ROLE app_user"))
        for tabla in _CATALOGOS_GLOBALES:
            fila = conn.execute(text(f"SELECT * FROM {tabla} LIMIT 1")).mappings().one()
            columna = "id" if "id" in fila else "clave"
            sp = conn.begin_nested()
            try:
                borradas = conn.execute(
                    text(f"DELETE FROM {tabla} WHERE {columna} = :k"),
                    {"k": fila[columna]},
                ).rowcount
                assert borradas == 0, f"app_user borró de {tabla}"
            except ProgrammingError as exc:            # sin GRANT: también vale
                assert "permission denied" in str(exc).lower()
            finally:
                sp.rollback()
        trans.rollback()
