"""SQLAlchemy models. Importing this package registers every table on
`Base.metadata` so Alembic autogenerate and metadata reflection see them.
"""
from .almacen import Almacen
from .categoria import CategoriaProducto
from .cliente import Cliente
from .cliente_externo import ClienteExterno
from .clave_sae import ClaveSae
from .conexion import Conexion
from .grupo_whatsapp import GrupoWhatsapp
from .import_productos_log import ImportProductosLog
from .conversion import ConversionProducto
from .esquema_impuesto import EsquemaImpuesto
from .cobranza_auto import CobranzaConfig, CobranzaContacto, CobranzaEnvio
from .espejo_sync import EspejoSync
from .devolucion import Devolucion, LineaDevolucion
from .factura import Factura, LineaFactura, TimbradoIntento
from .pago import Pago
from .recibo_pago import ReciboPago, ReciboPagoFactura
from .nota_credito import NotaCredito, NotaCreditoFactura
from .pos_corte import PosCorte
from .inventario import LoteInventario, Merma, MovimientoInventario
from .oc_recibida import OCRecibida
from .orden_compra import LineaOrdenCompra, OrdenCompra
from .permission import Permission
from .precio import ListaAsignacion, ListaPrecios, Precio
from .producto import Producto
from .producto_alias import ProductoAlias
from .producto_cliente import ProductoCliente
from .proyecto import Proyecto
from .sat_catalogo import SatClaveProdServ, SatClaveUnidad
from .proveedor import Proveedor
from .remision import LineaRemision, Remision
from .role import Role
from .role_permission import RolePermission
from .serie import Serie
from .solicitud_alta_sae import SolicitudAltaSae
from .sucursal import ClienteSucursal, ClienteSucursalSerie, PrecioOverride, Sucursal
from .tenant import Membership, Tenant, User
from .ticket import Ticket

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
    "EspejoSync",
    "CobranzaConfig",
    "CobranzaContacto",
    "CobranzaEnvio",
    "SolicitudAltaSae",
    "Ticket",
    "Producto",
    "ProductoAlias",
    "ProductoCliente",
    "ImportProductosLog",
    "SatClaveProdServ",
    "SatClaveUnidad",
    "ListaPrecios",
    "ListaAsignacion",
    "Precio",
    "Proyecto",
    "Cliente",
    "ClienteExterno",
    "ClaveSae",
    "Conexion",
    "GrupoWhatsapp",
    # ── Phase 4: operaciones ──
    "Proveedor",
    "Almacen",
    "LoteInventario",
    "MovimientoInventario",
    "Merma",
    "OrdenCompra",
    "OCRecibida",
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
    "ReciboPago",
    "ReciboPagoFactura",
    "NotaCredito",
    "NotaCreditoFactura",
    "PosCorte",
    "TimbradoIntento",
    # ── precios v2 ──
    "ClienteSucursal",
    "ClienteSucursalSerie",
    "Sucursal",
    "PrecioOverride",
    # ── series / folios ──
    "Serie",
]
