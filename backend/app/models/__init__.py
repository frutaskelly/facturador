"""SQLAlchemy models. Importing this package registers every table on
`Base.metadata` so Alembic autogenerate and metadata reflection see them.
"""
from .almacen import Almacen
from .categoria import CategoriaProducto
from .cliente import Cliente
from .conversion import ConversionProducto
from .esquema_impuesto import EsquemaImpuesto
from .devolucion import Devolucion, LineaDevolucion
from .factura import Factura, LineaFactura, TimbradoIntento
from .pago import Pago
from .pos_corte import PosCorte
from .inventario import LoteInventario, Merma, MovimientoInventario
from .orden_compra import LineaOrdenCompra, OrdenCompra
from .permission import Permission
from .precio import ListaPrecios, Precio
from .producto import Producto
from .producto_alias import ProductoAlias
from .proveedor import Proveedor
from .remision import LineaRemision, Remision
from .role import Role
from .role_permission import RolePermission
from .serie import Serie
from .sucursal import PrecioOverride, Sucursal
from .tenant import Membership, Tenant, User

__all__ = [
    "Tenant",
    "User",
    "Membership",
    "Role",
    "Permission",
    "RolePermission",
    # ── Phase 3: catálogo ──
    "CategoriaProducto",
    "EsquemaImpuesto",
    "Producto",
    "ProductoAlias",
    "ListaPrecios",
    "Precio",
    "Cliente",
    # ── Phase 4: operaciones ──
    "Proveedor",
    "Almacen",
    "LoteInventario",
    "MovimientoInventario",
    "Merma",
    "OrdenCompra",
    "LineaOrdenCompra",
    "ConversionProducto",
    "Remision",
    "LineaRemision",
    # ── Phase 6: fiscal ──
    "Devolucion",
    "Factura",
    "LineaDevolucion",
    "LineaFactura",
    "Pago",
    "PosCorte",
    "TimbradoIntento",
    # ── precios v2 ──
    "Sucursal",
    "PrecioOverride",
    # ── series / folios ──
    "Serie",
]
