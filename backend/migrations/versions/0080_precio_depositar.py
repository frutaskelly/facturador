"""Permiso acotado `precio:depositar`.

El chat cambia precios (~292 veces en 30 días) y hasta hoy lo hacía escribiendo
directo en `PRECIO_X_PROD` de SAE; el espejo los traía de vuelta. Con las listas
de precios viviendo en el Facturador (decisión del dueño, 20-sep-2026) esa vuelta
se corta, así que la clave del bot necesita poder DEPOSITAR un precio aquí.

Se crea un permiso nuevo en vez de darle `lista_precios:gestionar`, que además de
los precios abre crear listas, copiarlas, asignarlas a proyectos, importarlas y
—lo más delicado— EDITAR su vínculo con SAE (`sae_empresa`/`sae_lista`), o sea
re-encender el espejo que se acaba de apagar. `precio:depositar` solo escribe
precios en listas que YA existen.

Se siembra en el catálogo a propósito: `factura:espejo` nació solo en código y
quedó como sigla huérfana que ninguna pantalla de roles puede ofrecer. No se
repite el error.

Revision ID: 0080_precio_depositar
Revises: 0078_autoria_productos
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0080_precio_depositar"
down_revision: Union[str, None] = "0078_autoria_productos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "INSERT INTO permissions (id, recurso, accion, vertical, descripcion) "
        "VALUES ('precio:depositar', 'precio', 'depositar', NULL, "
        "'Depositar precios en listas existentes (no crea ni vincula listas)') "
        "ON CONFLICT (id) DO NOTHING"
    )
    # Quien ya administra listas conserva todo lo que podía hacer: los endpoints
    # de precio aceptan `lista_precios:gestionar` O este permiso, así que ningún
    # rol existente pierde acceso y no hace falta re-otorgar nada.


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DELETE FROM role_permissions WHERE permission_id = 'precio:depositar'"
    )
    op.get_bind().exec_driver_sql(
        "DELETE FROM permissions WHERE id = 'precio:depositar'"
    )
