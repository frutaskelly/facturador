"""Product catalog.

Tenant-scoped. References a category and a tax scheme by FK. Carries the SAT
fields needed for CFDI 4.0 (clave_prod_serv, clave_unidad, objeto_imp) plus
denormalized IVA/IEPS rates so invoice building stays join-free.

Dropped from v1 (clean rebuild): the `linea`/`categoria` string columns
(replaced by `categoria_id`), and the chat/WhatsApp NLU artifacts
`nombre_normalizado` and `aliases_clientes` (those modules are cut in v2).
`sinonimos` is kept for product search/matching in the POS.
"""
from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship

from ..core.db import Base
from .base import SoftDeleteMixin, TimestampMixin, tenant_fk, uuid_pk


class Producto(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "productos"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_producto_tenant_sku"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    sku = Column(String(50), nullable=False)
    nombre = Column(String(254), nullable=False)
    descripcion = Column(Text)

    categoria_id = Column(
        UUID(as_uuid=True),
        ForeignKey("categorias_producto.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    esquema_impuesto_id = Column(
        UUID(as_uuid=True),
        ForeignKey("esquemas_impuesto.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── SAT / CFDI 4.0 ──
    clave_sat = Column(String(8), nullable=False)   # ClaveProdServ
    unidad_sat = Column(String(3), nullable=False)  # ClaveUnidad
    objeto_imp = Column(String(2), nullable=False, server_default="02")
    # Denormalized rates (mirror the tax scheme at edit time) for the CFDI builder.
    iva_tasa = Column(Numeric(5, 4), nullable=False, server_default="0")
    ieps_tasa = Column(Numeric(5, 4), nullable=False, server_default="0")

    # ── units / presentations ──
    # `unidad_base` is the canonical inventory unit. `presentaciones` maps each
    # sellable/buyable presentation to how many base units it contains, e.g.
    # base KILO + {"KILO": 1, "BULTO": 20} → 1 BULTO = 20 KILO. Stock + costs are
    # always stored in base units; documents convert via the factor at stock time.
    unidad_base = Column(String(20), nullable=False, server_default="KILO")
    presentaciones = Column(JSONB, nullable=False, server_default='{"KILO": 1}')
    presentacion_default = Column(String(20), server_default="KILO")
    unidad_entrada = Column(String(20))
    unidad_salida = Column(String(20))
    # Catch-weight: when true, the presentation factor is only an estimate and
    # the real weight is captured at receiving/delivery (sandía, carnes al peso).
    peso_variable = Column(Boolean, nullable=False, server_default="false")
    codigo_barras = Column(String(20))            # EAN-13/GTIN of the consumer unit
    contenido_litros = Column(Numeric(10, 4))     # liters/pieza → base for IEPS cuota

    # ── inventory attributes ──
    perecedero = Column(Boolean, nullable=False, server_default="false")
    cold_chain = Column(Boolean, nullable=False, server_default="false")
    requiere_lote = Column(Boolean, nullable=False, server_default="false")
    requiere_caducidad = Column(Boolean, nullable=False, server_default="false")
    vida_util_dias = Column(Integer)
    # NB: el costo NO vive aquí — su verdad está en lotes_inventario.costo_unitario
    # (promedio ponderado por lote) y se consulta vía existencias por almacén.

    # Clave del artículo en SAE — la MISMA en todas sus empresas (decisión del
    # dueño, 18-sep-2026). Es la base: el catálogo del cliente sigue pudiendo
    # pisarla con una clave propia por plaza, pero ya no hace falta repetirla
    # cliente por cliente. NO es única: varios productos pueden compartir la
    # misma CVE_ART (regla del dueño, 23-sep-2026; la 0085 quitó el índice
    # único de la 0079). Lo que no puede haber es producto sin clave.
    clave_sae = Column(String(50))

    sinonimos = Column(ARRAY(Text), nullable=False, server_default="{}")
    activo = Column(Boolean, nullable=False, server_default="true")
    custom_fields = Column(JSONB, nullable=False, server_default="{}")

    # Autoría, como en remisiones. Nullable porque el seed y los scripts dan de
    # alta sin usuario, y una conexión del bot no tiene persona a quién atribuir.
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    categoria = relationship("CategoriaProducto")
    esquema_impuesto = relationship("EsquemaImpuesto")
