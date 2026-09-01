"""Remisiones — delivery notes (Phase 4e, lean core).

A remisión is a non-fiscal dispatch document: BORRADOR → CONFIRMADA → CANCELADA.
Una remisión con folio de factura de SAE (`factura_sae`) queda en RESERVADO:
mercancía comprometida con un comprobante que vive fuera del facturador. No
mueve inventario — la salida sigue siendo el confirmar.
Confirming reserves stock (disponible → reservada via a SALIDA_REMISION
movement); cancelling a confirmed one releases it. Folios are a per-tenant
`R-N` sequence for now — real fiscal series arrive in Phase 6.

Deliberately excluded here (arrive later): the POS operational overlay
(asignaciones caja/almacén/salida, surtido tracking, cobro) → Phase 5; the
fiscal coupling (factura_id, invoicing states, CFDI) → Phase 6; and v1's
AI/multichannel ingestion + government-contract links (cut from v2).
"""
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from typing import Optional

from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from ..core.db import Base
from .base import SoftDeleteMixin, TimestampMixin, tenant_fk, uuid_pk

REMISION_ESTADO = Enum(
    "BORRADOR", "RESERVADO", "CONFIRMADA", "FACTURADA", "CANCELADA", name="remision_estado"
)


class Remision(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "remisiones"
    __table_args__ = (
        UniqueConstraint("tenant_id", "folio_interno", name="uq_remision_tenant_folio"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    folio_interno = Column(String(20), nullable=False)
    cliente_facturacion_id = Column(
        UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    almacen_id = Column(UUID(as_uuid=True), ForeignKey("almacenes.id", ondelete="SET NULL"))
    sucursal_id = Column(UUID(as_uuid=True), ForeignKey("sucursales.id", ondelete="SET NULL"))
    # Lista FORZADA a mano para este documento: le gana a toda la resolución por
    # cliente/sucursal/serie/proyecto. NULL = que resuelva el sistema.
    lista_precios_id = Column(UUID(as_uuid=True), ForeignKey("listas_precios.id", ondelete="SET NULL"))
    # La negociación bajo la que se vendió (viene de la orden de compra).
    proyecto_id = Column(UUID(as_uuid=True), ForeignKey("proyectos.id", ondelete="SET NULL"))
    # La serie con la que se folió. Se guarda porque además de dar el folio,
    # decide qué lista de precios aplica: al reeditar hay que usar la misma.
    serie_id = Column(UUID(as_uuid=True), ForeignKey("series.id", ondelete="SET NULL"))
    fecha_remision = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    fecha_entrega = Column(Date)
    estado = Column(REMISION_ESTADO, nullable=False, server_default="BORRADOR")
    canal = Column(String(20), nullable=False, server_default="MANUAL")
    # Ancla de idempotencia de la ingesta automática ('WA:<jid>:<folio de OC>').
    # UNIQUE parcial por (tenant, origen_externo): un reintento del bot tras un
    # timeout devuelve la remisión que ya existe en vez de crear otra y quemar
    # un folio de la serie. NULL en todo lo capturado a mano.
    origen_externo = Column(String(120))
    subtotal = Column(Numeric(18, 4), nullable=False, server_default="0")
    descuento = Column(Numeric(18, 4), nullable=False, server_default="0")
    iva = Column(Numeric(18, 4), nullable=False, server_default="0")
    ieps = Column(Numeric(18, 4), nullable=False, server_default="0")
    total = Column(Numeric(18, 4), nullable=False, server_default="0")
    notas = Column(Text)
    nota_entrega = Column(Text)
    # Fase 6: una factura cruza una o varias remisiones (NULL = sin facturar).
    factura_id = Column(UUID(as_uuid=True), ForeignKey("facturas.id", ondelete="SET NULL"))
    # Folio de la factura que ampara esta remisión en SAE ("ZHGO 233"). Es de
    # OTRO sistema: texto libre, sin FK. Tenerlo pone la remisión en RESERVADO.
    # Lo escribe el ESPEJO (cuando la factura existe en SAE) o una captura
    # manual — nunca el export masivo, cuyo archivo puede no subirse jamás.
    factura_sae = Column(String(30))
    # Rastro del export masivo: cuándo salió y con qué folio PROPUESTO
    # ("ZHGO 588"). NO implica que la factura exista — solo alimenta el aviso
    # de doble export y el folio sugerido del siguiente lote.
    export_sae_at = Column(DateTime(timezone=True))
    export_sae_folio = Column(String(30))
    # "Su pedido": la ORDEN DE COMPRA del cliente ("24478"), con la que él
    # reconoce el documento. Texto libre: es un folio de su sistema, no del nuestro.
    su_pedido = Column(String(30))
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    # Llegó de la bandeja SIN que nadie la revisara ("pasar sin revisar"): la
    # remisión ya tiene folio, pero sus unidades y precios no los ha visto un
    # humano. Mientras esté encendida no se confirma, no se factura y no sale al
    # export de SAE — el freno ES la razón de que se pueda pasar sin revisar.
    revision_pendiente = Column(Boolean, nullable=False, server_default=text("false"))
    # Las partidas de la orden que NO cruzaron a ningún producto, tal como
    # venían: [{numero, descripcion, cantidad, unidad, clave, precio, notas}].
    # No pueden ser líneas (`producto_id` es NOT NULL y de él cuelgan precio,
    # impuesto e inventario), pero tampoco se tiran: se cruzan a mano al revisar.
    partidas_por_cruzar = Column(JSONB, nullable=False, server_default="[]")

    # POS (Fase 0): estación donde el pedido ESPERA (pedido/caja/almacen/salida/
    # completado); NULL = remisión normal fuera del POS. El pipeline activo lo
    # define tenants.config.pos. `pos_asignaciones` = {etapa: {user_id, at}}.
    pos_etapa = Column(String(30), index=True)
    pos_asignaciones = Column(JSONB, nullable=False, server_default="{}")

    lineas = relationship(
        "LineaRemision",
        cascade="all, delete-orphan",
        back_populates="remision",
        order_by="LineaRemision.numero_linea",
    )
    # `factura_id` apunta a la ÚLTIMA factura de la remisión y NO se anula al
    # cancelarla (para poder mostrar su folio/estado en la lista). La
    # refacturabilidad se deriva del estado de esa factura (CANCELADA → libre).
    factura = relationship("Factura", foreign_keys=[factura_id])
    devoluciones = relationship("Devolucion", order_by="Devolucion.created_at")

    # La OC original que dio origen a la remisión (bandeja de órdenes). No es
    # una relación: se resuelve en lote en la lista y el detalle —por
    # `remision_id` o por el folio del cliente— para no hacer una consulta por
    # renglón contra la base en la nube.
    oc_id = None
    oc_archivo_url = None
    oc_archivo_nombre = None

    @property
    def factura_folio(self) -> Optional[str]:
        f = self.factura
        return f"{f.serie or ''}{f.folio}" if f else None

    @property
    def factura_estado(self) -> Optional[str]:
        return self.factura.estado if self.factura else None


class LineaRemision(Base):
    __tablename__ = "lineas_remision"
    __table_args__ = (
        UniqueConstraint("remision_id", "numero_linea", name="uq_linea_remision_numero"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    remision_id = Column(
        UUID(as_uuid=True), ForeignKey("remisiones.id", ondelete="CASCADE"), nullable=False, index=True
    )
    numero_linea = Column(SmallInteger, nullable=False)
    producto_id = Column(
        UUID(as_uuid=True), ForeignKey("productos.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    presentacion = Column(String(20), nullable=False, server_default="KILO")
    cantidad_solicitada = Column(Numeric(18, 4), nullable=False)
    cantidad_surtida = Column(Numeric(18, 4))
    precio_unitario = Column(Numeric(18, 4), nullable=False)
    importe = Column(Numeric(18, 4), nullable=False, server_default="0")
    # Impuestos informativos de la línea (decisión 2026-07-29: la remisión
    # muestra impuestos). Calculados con services/fiscal.calcular_linea_producto
    # — el mismo cerebro que las facturas; el CFDI recalcula al facturar.
    iva_importe = Column(Numeric(18, 4), nullable=False, server_default="0")
    ieps_importe = Column(Numeric(18, 4), nullable=False, server_default="0")
    lote_id = Column(UUID(as_uuid=True), ForeignKey("lotes_inventario.id", ondelete="SET NULL"))
    notas = Column(Text)

    remision = relationship("Remision", back_populates="lineas")
