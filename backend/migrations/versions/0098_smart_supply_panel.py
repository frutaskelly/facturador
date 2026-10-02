"""Panel de Smart Supply: conexión de solo lectura por cuenta + `abasto:leer`.

Smart Supply (app.smartsupply.mx) mide la merma de cada bodega y lee de aquí,
por API y con una clave POR CUENTA (plaza), las OC, lo remisionado y lo
facturado. La conexión es un tipo nuevo, `SMART_SUPPLY_PANEL`, cuya clave trae
SOLO `abasto:leer` (ver `PERMISOS_POR_TIPO` en core/rbac.py).

- El permiso se siembra aquí, por la lección de 0090: un permiso que solo vive
  en código es una sigla huérfana que la pantalla de roles no puede ofrecer.
  No se le asigna a ningún rol humano; OWNER lo tiene por bypass.
- El panel va por cuenta, como Mini Conta: el índice «una viva por tipo» deja de
  aplicarle, y dos cuentas vivas del MISMO tipo no pueden llamarse igual (entre
  tipos sí: «Kelly Tabasco» de Mini Conta y la del panel son la misma plaza).

`conexiones.tipo` es VARCHAR sin CHECK y `alcance` ya es JSONB (0092): el tipo
nuevo no necesita columnas.

Revision ID: 0098_smart_supply_panel
Revises: 0097_grupos_cobranza
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0098_smart_supply_panel"
down_revision: Union[str, None] = "0097_grupos_cobranza"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_POR_CUENTA = "('MINI_CONTA', 'SMART_SUPPLY_PANEL')"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "INSERT INTO permissions (id, recurso, accion, vertical, descripcion) "
        "VALUES ('abasto:leer', 'abasto', 'leer', NULL, "
        "'Leer OC, remisionado y facturado de una plaza (conexión del panel de "
        "Smart Supply; solo lectura)') "
        "ON CONFLICT (id) DO NOTHING"
    )
    op.execute("DROP INDEX IF EXISTS uq_conexion_viva_por_tipo")
    op.execute(
        "CREATE UNIQUE INDEX uq_conexion_viva_por_tipo ON conexiones (tenant_id, tipo) "
        f"WHERE estado <> 'REVOCADA' AND tipo NOT IN {_POR_CUENTA}"
    )
    op.execute("DROP INDEX IF EXISTS uq_conexion_mini_conta_nombre")
    op.execute(
        "CREATE UNIQUE INDEX uq_conexion_cuenta_nombre "
        "ON conexiones (tenant_id, tipo, lower(nombre)) "
        f"WHERE estado <> 'REVOCADA' AND tipo IN {_POR_CUENTA}"
    )


def downgrade() -> None:
    # Sin el tipo, sus claves no tienen alcance que respetar: se revocan antes
    # de volver a «una viva por tipo», que si no truena con dos cuentas vivas.
    op.execute(
        "UPDATE conexiones SET estado = 'REVOCADA', revocada_at = now() "
        "WHERE tipo = 'SMART_SUPPLY_PANEL' AND estado <> 'REVOCADA'"
    )
    op.execute("DROP INDEX IF EXISTS uq_conexion_cuenta_nombre")
    op.execute(
        "CREATE UNIQUE INDEX uq_conexion_mini_conta_nombre "
        "ON conexiones (tenant_id, lower(nombre)) "
        "WHERE estado <> 'REVOCADA' AND tipo = 'MINI_CONTA'"
    )
    op.execute("DROP INDEX IF EXISTS uq_conexion_viva_por_tipo")
    op.execute(
        "CREATE UNIQUE INDEX uq_conexion_viva_por_tipo ON conexiones (tenant_id, tipo) "
        "WHERE estado <> 'REVOCADA' AND tipo <> 'MINI_CONTA'"
    )
    op.get_bind().exec_driver_sql(
        "DELETE FROM role_permissions WHERE permission_id = 'abasto:leer'"
    )
    op.get_bind().exec_driver_sql("DELETE FROM permissions WHERE id = 'abasto:leer'")
