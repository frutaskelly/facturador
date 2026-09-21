"""Permiso angosto `producto:alta_sae` para la conexión del bot.

La meta del dueño es que el alta de productos vaya «WhatsApp → Facturador →
SAE». Para eso la conexión tiene que poder PEDIR un alta y crear el producto
nuevo que la acompaña — pero `producto:gestionar` está fuera de
PERMISOS_CONEXION a propósito, y por una razón que sigue siendo válida:
reapuntar un alias afecta a TODO el catálogo.

Así que se repite lo que se hizo con `precio:depositar` (0080): un permiso que
solo abre lo que hace falta. Con este, la conexión puede crear un producto
nuevo dentro de una solicitud de alta y encolarla; no puede editar, reapuntar
ni dar de baja nada de lo que ya existe.

Se siembra en el catálogo de permisos a propósito, por la misma lección: un
permiso que solo vive en código queda como sigla huérfana que ninguna pantalla
de roles puede ofrecer.

Revision ID: 0082_producto_alta_sae
Revises: 0081_solicitudes_alta_sae
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0082_producto_alta_sae"
down_revision: Union[str, None] = "0081_solicitudes_alta_sae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "INSERT INTO permissions (id, recurso, accion, vertical, descripcion) "
        "VALUES ('producto:alta_sae', 'producto', 'alta_sae', NULL, "
        "'Pedir el alta de un producto en SAE (crea el producto nuevo que la "
        "acompaña; no edita ni reapunta el catálogo existente)') "
        "ON CONFLICT (id) DO NOTHING"
    )
    # Quien ya administra el catálogo conserva todo: los endpoints aceptan
    # `producto:gestionar` O este permiso, así que ningún rol pierde acceso.


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DELETE FROM role_permissions WHERE permission_id = 'producto:alta_sae'"
    )
    op.get_bind().exec_driver_sql(
        "DELETE FROM permissions WHERE id = 'producto:alta_sae'"
    )
