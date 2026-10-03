"""Bitácora de conexiones: quién creó, cambió, rotó o desconectó cada clave.

Desde oct-2026 lo que comparte una cuenta del panel de Smart Supply se corrige
en su lugar («Qué comparte», sin clave nueva) y Smart Supply lo aplica en su
siguiente vuelta. Sin bitácora, quitarle una serie a una plaza no dejaba
rastro: la pregunta «¿quién le quitó RRIO a Kelly Hidalgo y cuándo?» no tenía
respuesta.

Append-only (app_user solo lee e inserta: se le REVOCAN el UPDATE y el DELETE
que el esquema da por omisión) y por inquilino (RLS como el resto). Nunca
guarda la clave: solo su pista (los últimos 4), que ya se enseña en la pantalla.

DDL con IF NOT EXISTS: la BD de pruebas (:5434) la comparten varias sesiones y
la tabla se le puede crear a mano sin mover su `alembic_version`. Por eso todo
`upgrade()` se puede correr dos veces.

Revision ID: 0101_conexion_cambios
Revises: 0100_lista_sku_cliente
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0101_conexion_cambios"
down_revision: Union[str, None] = "0100_lista_sku_cliente"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conexion_cambios (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            conexion_id uuid NOT NULL REFERENCES conexiones(id) ON DELETE CASCADE,
            accion varchar(20) NOT NULL,
            nombre_antes varchar(80),
            nombre_despues varchar(80),
            alcance_antes jsonb,
            alcance_despues jsonb,
            reemplaza_a uuid REFERENCES conexiones(id) ON DELETE SET NULL,
            clave_pista varchar(8),
            hecho_por uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # La única consulta: la historia de una conexión, la más nueva primero.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_conexion_cambios_conexion "
        "ON conexion_cambios (conexion_id, created_at DESC)"
    )
    op.execute("GRANT SELECT, INSERT ON conexion_cambios TO app_user")
    # Append-only de verdad. El GRANT de arriba no quita nada: los privilegios
    # por omisión del esquema ya le dan UPDATE y DELETE a app_user (prod:
    # pg_default_acl postgres/public = app_user=arwd) y todo a `anon` y
    # `authenticated`, los roles de PostgREST que el backend no usa. RLS cuida
    # el inquilino; esto, que nadie reescriba ni borre la historia.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON conexion_cambios FROM app_user")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                REVOKE ALL ON conexion_cambios FROM anon;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                REVOKE ALL ON conexion_cambios FROM authenticated;
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE conexion_cambios ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                 WHERE tablename = 'conexion_cambios' AND policyname = 'tenant_isolation'
            ) THEN
                CREATE POLICY tenant_isolation ON conexion_cambios
                    USING (tenant_id = public.current_tenant_id());
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON conexion_cambios")
    op.execute("DROP TABLE IF EXISTS conexion_cambios")
