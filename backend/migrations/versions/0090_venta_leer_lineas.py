"""Permiso `venta:leer_lineas` para la conexión de Mini Conta.

Mini Conta (contabilidad por sucursal) lee las líneas facturadas de una plaza
para cruzarlas contra sus compras. Su clave de conexión trae SOLO este permiso
(ver `PERMISOS_POR_TIPO` en core/rbac.py): no abre la bandeja, ni remisiones,
ni las pantallas de facturas.

Se siembra en el catálogo por la misma lección de 0080/0082: un permiso que solo
vive en código queda como sigla huérfana que ninguna pantalla de roles puede
ofrecer. No se le asigna a ningún rol humano; OWNER lo tiene por bypass.

La tabla `conexiones.tipo` es VARCHAR sin CHECK, así que el tipo nuevo
`MINI_CONTA` no necesita cambio de esquema.

Revision ID: 0090_venta_leer_lineas
Revises: 0089_cambios_sae
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0090_venta_leer_lineas"
down_revision: Union[str, None] = "0089_cambios_sae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "INSERT INTO permissions (id, recurso, accion, vertical, descripcion) "
        "VALUES ('venta:leer_lineas', 'venta', 'leer_lineas', NULL, "
        "'Leer las líneas facturadas por sucursal (conexión de Mini Conta; "
        "solo lectura)') "
        "ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DELETE FROM role_permissions WHERE permission_id = 'venta:leer_lineas'"
    )
    op.get_bind().exec_driver_sql(
        "DELETE FROM permissions WHERE id = 'venta:leer_lineas'"
    )
