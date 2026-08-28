"""Export sin estampa + RBAC por menú (ver/editar/borrar) + portal de cliente

Revision ID: 0057_export_rastro_portal_rbac
Revises: 0056_sucursal_series
Create Date: 2026-08-29

Tres piezas de la misma decisión del dueño (29-ago-2026):

1. `remisiones.export_sae_at`: el export masivo YA NO estampa `factura_sae`
   (un archivo que nunca se subió dejaba folios fantasma como "ZHGO 588");
   solo deja este rastro para avisar de un doble export. La verdad la pone
   el espejo cuando la factura existe en SAE.

2. Permisos nuevos para que los roles se administren POR MENÚ con las tres
   acciones ver/editar/borrar:
   - `menu:cotizador` (antes el cotizador colgaba de menu:listas_precios, que
     arrastra TODAS las listas) y `menu:oc` (la bandeja compartía permiso con
     remisiones — un usuario de cliente no debe ver las órdenes de otros).
   - `<recurso>:eliminar` separado de `<recurso>:gestionar` en los recursos
     con DELETE real, y `factura:cancelar` separado (cancelar un CFDI no es
     lo mismo que editar facturas).
   Los roles que hoy tienen `gestionar` reciben `eliminar` (sin cambio de
   comportamiento); ídem los menús nuevos.

3. `memberships.cliente_scope` (UUID[]): limita a un usuario a UNO o VARIOS
   clientes — el operador del cliente (Balles/Jubran) solo ve sus remisiones,
   facturas y precios. NULL = sin límite. Y el rol preset PORTAL CLIENTE:
   cotizador + vistas de solo lectura.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0057_export_rastro_portal_rbac"
down_revision: Union[str, None] = "0056_sucursal_series"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Recursos cuyo DELETE se separa de gestionar. sucursales/proyectos/overrides
# siguen bajo su gestionar: son sub-entidades de la edición del cliente/lista.
_ELIMINAR = (
    "producto", "categoria", "esquema_impuesto", "lista_precios", "cliente",
    "proveedor", "almacen", "conversion", "remision", "serie", "role",
    "membership", "factura",
)

_PORTAL_PERMS = ("menu:cotizador", "menu:clientes", "menu:remisiones", "menu:facturas")


def upgrade() -> None:
    op.add_column("remisiones", sa.Column("export_sae_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "memberships",
        sa.Column("cliente_scope", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
    )

    # ── catálogo de permisos ──
    filas = [
        ("menu:cotizador", "menu", "cotizador", "Cotizador"),
        ("menu:oc", "menu", "oc", "Bandeja de órdenes"),
        ("factura:cancelar", "factura", "cancelar", "Cancelar/sustituir facturas timbradas"),
    ] + [(f"{r}:eliminar", r, "eliminar", "Eliminar") for r in _ELIMINAR]
    for pid, recurso, accion, desc in filas:
        op.get_bind().exec_driver_sql(
            "INSERT INTO permissions (id, recurso, accion, vertical, descripcion) "
            f"VALUES ('{pid}', '{recurso}', '{accion}', NULL, '{desc}') "
            "ON CONFLICT (id) DO NOTHING"
        )

    # ── seeds: sin cambio de comportamiento para los roles existentes ──
    # gestionar → también eliminar (presets Y personalizados)
    op.get_bind().exec_driver_sql(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT rp.role_id, replace(rp.permission_id, ':gestionar', ':eliminar')
        FROM role_permissions rp
        WHERE rp.permission_id IN ({})
        ON CONFLICT DO NOTHING
        """.format(", ".join(f"'{r}:gestionar'" for r in _ELIMINAR))
    )
    op.get_bind().exec_driver_sql(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT rp.role_id, 'factura:cancelar' FROM role_permissions rp
        WHERE rp.permission_id = 'factura:gestionar'
        ON CONFLICT DO NOTHING
        """
    )
    # el cotizador lo usaban quienes veían listas de precios O productos
    op.get_bind().exec_driver_sql(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT DISTINCT rp.role_id, 'menu:cotizador' FROM role_permissions rp
        WHERE rp.permission_id IN ('menu:listas_precios', 'menu:productos')
        ON CONFLICT DO NOTHING
        """
    )
    # la bandeja la veían quienes veían remisiones
    op.get_bind().exec_driver_sql(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT rp.role_id, 'menu:oc' FROM role_permissions rp
        WHERE rp.permission_id = 'menu:remisiones'
        ON CONFLICT DO NOTHING
        """
    )

    # ── rol preset PORTAL CLIENTE ──
    op.get_bind().exec_driver_sql(
        """
        INSERT INTO roles (tenant_id, nombre, vertical, es_preset, descripcion)
        SELECT NULL, 'PORTAL CLIENTE', NULL, true,
               'Operador del cliente: cotiza contra su lista y ve (solo lectura) sus remisiones y facturas'
        WHERE NOT EXISTS (
            SELECT 1 FROM roles WHERE nombre = 'PORTAL CLIENTE' AND es_preset = true AND tenant_id IS NULL
        )
        """
    )
    op.get_bind().exec_driver_sql(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.pid
        FROM roles r, (VALUES {}) AS p(pid)
        WHERE r.nombre = 'PORTAL CLIENTE' AND r.es_preset = true AND r.tenant_id IS NULL
        ON CONFLICT DO NOTHING
        """.format(", ".join(f"('{p}')" for p in _PORTAL_PERMS))
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DELETE FROM role_permissions WHERE role_id IN "
        "(SELECT id FROM roles WHERE nombre = 'PORTAL CLIENTE' AND es_preset = true AND tenant_id IS NULL)"
    )
    op.get_bind().exec_driver_sql(
        "DELETE FROM roles WHERE nombre = 'PORTAL CLIENTE' AND es_preset = true AND tenant_id IS NULL"
    )
    nuevos = ["menu:cotizador", "menu:oc", "factura:cancelar"] + [f"{r}:eliminar" for r in _ELIMINAR]
    lista = ", ".join(f"'{p}'" for p in nuevos)
    op.get_bind().exec_driver_sql(f"DELETE FROM role_permissions WHERE permission_id IN ({lista})")
    op.get_bind().exec_driver_sql(f"DELETE FROM permissions WHERE id IN ({lista})")
    op.drop_column("memberships", "cliente_scope")
    op.drop_column("remisiones", "export_sae_at")
