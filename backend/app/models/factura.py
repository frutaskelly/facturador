"""Facturas (CFDI 4.0) — Fase 6.

Una factura agrupa el desglose fiscal calculado por concepto (IVA/IEPS/
retenciones) y, una vez timbrada, el UUID/XML/PDF que devuelve el PAC. Cruza una
o varias remisiones (remision.factura_id). El cálculo vive en services/fiscal.py
y el timbrado en services/facturama.py (P6.2).
"""
from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ..core.db import Base
from .base import SoftDeleteMixin, TimestampMixin, tenant_fk, uuid_pk

FACTURA_ESTADO = Enum("BORRADOR", "TIMBRADA", "CANCELADA", name="factura_estado")


class Factura(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "facturas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "serie", "folio", name="uq_factura_tenant_serie_folio"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    serie = Column(String(10), nullable=False, server_default="F")
    folio = Column(Integer, nullable=False)
    cliente_id = Column(UUID(as_uuid=True), ForeignKey("clientes.id", ondelete="RESTRICT"), nullable=False, index=True)
    # Solo facturas DIRECTAS (sin remisión): almacén del que descuentan inventario
    # al timbrar y al que regresan al cancelar. Null en facturas desde remisiones.
    almacen_id = Column(UUID(as_uuid=True), ForeignKey("almacenes.id", ondelete="RESTRICT"), nullable=True, index=True)
    # La negociación bajo la que se vendió; se hereda de las remisiones que
    # cruza. Sirve para leer la facturación por proyecto sin rearmarla a mano.
    proyecto_id = Column(UUID(as_uuid=True), ForeignKey("proyectos.id", ondelete="SET NULL"))

    # ── CFDI 4.0 header ──
    uso_cfdi = Column(String(5), nullable=False, server_default="G03")
    forma_pago = Column(String(5), nullable=False, server_default="99")
    metodo_pago = Column(String(5), nullable=False, server_default="PUE")
    moneda = Column(String(3), nullable=False, server_default="MXN")
    tipo_comprobante = Column(String(1), nullable=False, server_default="I")
    lugar_expedicion = Column(String(5))
    fecha = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    # ── totales ──
    subtotal = Column(Numeric(18, 4), nullable=False, server_default="0")
    descuento = Column(Numeric(18, 4), nullable=False, server_default="0")
    iva_trasladado = Column(Numeric(18, 4), nullable=False, server_default="0")
    ieps_trasladado = Column(Numeric(18, 4), nullable=False, server_default="0")
    ret_iva = Column(Numeric(18, 4), nullable=False, server_default="0")
    ret_isr = Column(Numeric(18, 4), nullable=False, server_default="0")
    total = Column(Numeric(18, 4), nullable=False, server_default="0")
    # Cobranza: lo que falta cobrar de una PPD (arranca = total al timbrar, baja
    # con cada abono/REP). PUE / no-PPD quedan en 0. Base del estado de cuenta.
    saldo_insoluto = Column(Numeric(18, 4), nullable=False, server_default="0")

    # ── timbrado (lo llena el PAC en P6.2) ──
    estado = Column(FACTURA_ESTADO, nullable=False, server_default="BORRADOR")
    uuid = Column(String(36))
    facturama_id = Column(String(40))
    fecha_timbrado = Column(DateTime(timezone=True))
    xml = Column(Text)
    pdf_url = Column(Text)

    # ── cancelación CFDI ──
    fecha_cancelacion = Column(DateTime(timezone=True))
    motivo_cancelacion = Column(String(2))      # 01-04 (CFDI 4.0)
    # Sentido VIEJA → NUEVA: al cancelar esta factura con motivo "01", el UUID del
    # CFDI que la sustituye (se reporta al SAT como uuidReplacement).
    uuid_sustitucion = Column(String(36))
    # Sentido NUEVA → VIEJA: la factura previa a la que ESTA sustituye. Al timbrar,
    # emite el nodo Relations TipoRelacion "04" con el UUID de esa factura vieja.
    sustituye_a_factura_id = Column(
        UUID(as_uuid=True), ForeignKey("facturas.id", ondelete="SET NULL"), nullable=True, index=True
    )

    notas = Column(Text)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    lineas = relationship(
        "LineaFactura", cascade="all, delete-orphan", back_populates="factura",
        order_by="LineaFactura.numero_linea",
    )
    cliente = relationship("Cliente")
    # La factura vieja que ESTA sustituye (nueva → vieja).
    sustituye_a = relationship("Factura", remote_side="Factura.id", foreign_keys=[sustituye_a_factura_id])


class LineaFactura(Base):
    __tablename__ = "lineas_factura"
    __table_args__ = (
        UniqueConstraint("factura_id", "numero_linea", name="uq_linea_factura_numero"),
    )

    id = uuid_pk()
    tenant_id = tenant_fk()
    factura_id = Column(UUID(as_uuid=True), ForeignKey("facturas.id", ondelete="CASCADE"), nullable=False, index=True)
    numero_linea = Column(SmallInteger, nullable=False)
    producto_id = Column(UUID(as_uuid=True), ForeignKey("productos.id", ondelete="RESTRICT"), nullable=False)

    clave_prod_serv = Column(String(8), nullable=False)
    clave_unidad = Column(String(3), nullable=False)
    descripcion = Column(String(1000), nullable=False)
    cantidad = Column(Numeric(18, 6), nullable=False)
    # Factura directa: cantidad en unidad base y lote afectado, para descontar al
    # timbrar y revertir al cancelar. Null en líneas de facturas desde remisiones.
    cantidad_base = Column(Numeric(18, 4))
    lote_id = Column(UUID(as_uuid=True), ForeignKey("lotes_inventario.id", ondelete="SET NULL"))
    valor_unitario = Column(Numeric(18, 6), nullable=False)
    importe = Column(Numeric(18, 4), nullable=False, server_default="0")
    descuento = Column(Numeric(18, 4), nullable=False, server_default="0")
    objeto_imp = Column(String(2), nullable=False, server_default="02")

    iva_tasa = Column(Numeric(5, 4), nullable=False, server_default="0")
    iva_importe = Column(Numeric(18, 4), nullable=False, server_default="0")
    ieps_tipo = Column(String(10))   # 'TASA' | 'CUOTA' | None
    ieps_valor = Column(Numeric(12, 6), nullable=False, server_default="0")
    ieps_importe = Column(Numeric(18, 4), nullable=False, server_default="0")
    ret_iva_importe = Column(Numeric(18, 4), nullable=False, server_default="0")
    ret_isr_importe = Column(Numeric(18, 4), nullable=False, server_default="0")

    factura = relationship("Factura", back_populates="lineas")


class TimbradoIntento(Base, TimestampMixin):
    """Bitácora de intentos de timbrado (decisión 2026-07-29 #1).

    Se persiste (commit propio) ANTES de llamar al PAC y se marca al terminar.
    Un intento que queda en PENDIENTE = el servidor murió a media llamada; el
    próximo timbrado primero reconcilia contra Facturama (¿el CFDI ya existe?)
    antes de volver a timbrar, para no generar un duplicado ante el SAT.
    """

    __tablename__ = "timbrado_intentos"

    id = uuid_pk()
    tenant_id = tenant_fk()
    # Un intento es de una factura O de un recibo de pago (REP).
    factura_id = Column(
        UUID(as_uuid=True), ForeignKey("facturas.id", ondelete="CASCADE"), nullable=True, index=True
    )
    recibo_pago_id = Column(
        UUID(as_uuid=True), ForeignKey("recibos_pago.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # PENDIENTE (en vuelo) | TIMBRADA (éxito) | ERROR (el PAC rechazó / no llegó)
    estado = Column(String(10), nullable=False, server_default="PENDIENTE")
    detalle = Column(Text)
