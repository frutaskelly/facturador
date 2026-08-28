"""Factura (CFDI 4.0) schemas."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field

from .common import ORMModel


class EnviarFacturaIn(BaseModel):
    to: List[EmailStr] = Field(min_length=1, max_length=10)
    mensaje: Optional[str] = Field(default=None, max_length=2000)


class FacturaDesdeRemisionesIn(BaseModel):
    remision_ids: List[uuid.UUID] = Field(min_length=1, max_length=200)
    # Override manual de serie al emitir; si es None se resuelve por sucursal/cliente/default.
    serie_id: Optional[uuid.UUID] = None
    serie: Optional[str] = Field(default=None, max_length=10)  # back-compat por código
    uso_cfdi: Optional[str] = Field(default=None, max_length=5)
    forma_pago: Optional[str] = Field(default=None, max_length=5)
    metodo_pago: Optional[str] = Field(default=None, max_length=5)
    notas: Optional[str] = None
    # True = suma las líneas del mismo producto/unidad en un solo concepto.
    agrupar_productos: bool = Field(default=False)
    # Autoriza sobregiro al auto-confirmar remisiones en BORRADOR sin existencia
    # suficiente (el inventario queda en negativo). Ignorado para CONFIRMADAS.
    permitir_negativos: bool = Field(default=False)


class SustituirIn(BaseModel):
    """Crea la factura sustituta (refacturación) como copia de la vieja, ligada a
    ella con relación CFDI "04". Opcionalmente corrige los datos fiscales de
    cabecera que suelen ser el error a subsanar (uso de CFDI, forma/método de
    pago). El importe/conceptos se copian tal cual de la factura original."""
    uso_cfdi: Optional[str] = Field(default=None, max_length=5)
    forma_pago: Optional[str] = Field(default=None, max_length=5)
    metodo_pago: Optional[str] = Field(default=None, max_length=5)
    serie_id: Optional[uuid.UUID] = None
    notas: Optional[str] = None


class TimbrarIn(BaseModel):
    # Sobregiro autorizado: una factura DIRECTA sin existencia suficiente frena
    # con 422 salvo que esto venga en true (misma política que remisiones —
    # decisión 2026-07-29 #4). Ignorado en facturas desde remisiones.
    permitir_negativos: bool = False


class CancelarFacturaIn(BaseModel):
    # 01 errores con relación (requiere uuid_sustitucion) | 02 sin relación |
    # 03 no se llevó a cabo | 04 operación nominativa en factura global
    motivo: str = Field(default="02", pattern="^0[1-4]$")
    uuid_sustitucion: Optional[uuid.UUID] = None
    # Qué hacer con el inventario reservado por las remisiones de la factura:
    #  - "devolucion": regresa a disponible; las remisiones vuelven a BORRADOR.
    #  - "perdida": se da de baja como MERMA (no regresa); remisiones CANCELADAS.
    inventario: Literal["devolucion", "perdida"] = "devolucion"


class LineaFacturaDirectaIn(BaseModel):
    producto_id: uuid.UUID
    cantidad: Decimal = Field(gt=0)
    precio_unitario: Decimal = Field(ge=0)
    presentacion: Optional[str] = Field(default=None, max_length=20)


class FacturaDirectaIn(BaseModel):
    """Factura capturada a mano (sin remisión). Descuenta inventario del almacén
    indicado al timbrar y lo regresa al cancelar."""
    cliente_id: uuid.UUID
    almacen_id: uuid.UUID                 # de dónde sale el inventario al timbrar
    serie_id: Optional[uuid.UUID] = None
    serie: Optional[str] = Field(default=None, max_length=10)
    uso_cfdi: Optional[str] = Field(default=None, max_length=5)
    forma_pago: Optional[str] = Field(default=None, max_length=5)
    metodo_pago: Optional[str] = Field(default=None, max_length=5)
    notas: Optional[str] = None
    lineas: List[LineaFacturaDirectaIn] = Field(min_length=1)


class LineaFacturaEspejoIn(BaseModel):
    """Una partida tal como SAE la facturó. `clave` es la CVE_ART de SAE: si
    cruza con producto_clientes se liga al producto; si no, la línea se guarda
    igual con su descripción — el espejo no pierde renglones."""
    clave: Optional[str] = Field(default=None, max_length=30)
    descripcion: str = Field(max_length=1000)
    cantidad: Decimal = Field(gt=0)
    precio_unitario: Decimal = Field(ge=0)
    importe: Optional[Decimal] = Field(default=None, ge=0)


class FacturaEspejoIn(BaseModel):
    """Reflejo de una factura EMITIDA POR SAE (fase espejo de la migración).

    Idempotente por (serie, folio): re-mandarla actualiza el reflejo (estado,
    UUID, totales) — así es como llegan las cancelaciones de SAE. Nunca toca
    al PAC ni consume folios propios: serie y folio son los REALES de SAE.
    """
    empresa: str = Field(max_length=4)              # empresa SAE ("02")
    serie: str = Field(max_length=10)
    folio: int = Field(gt=0)
    # El cliente llega como su número en SAE; se resuelve con la equivalencia
    # 'empresa:numero' de cliente_externos (la misma que usa el export).
    cliente_sae: str = Field(max_length=10)
    fecha: Optional[datetime] = None
    estado: str = Field(default="TIMBRADA", pattern="^(TIMBRADA|CANCELADA)$")
    uuid_fiscal: Optional[str] = Field(default=None, max_length=36)   # CFDI02.UUID
    metodo_pago: Optional[str] = Field(default=None, max_length=5)
    forma_pago: Optional[str] = Field(default=None, max_length=5)
    uso_cfdi: Optional[str] = Field(default=None, max_length=5)
    observaciones: Optional[str] = None
    # Totales COMO LOS REPORTA SAE (la verdad es SAE; no se recalculan aquí).
    subtotal: Optional[Decimal] = Field(default=None, ge=0)
    total: Optional[Decimal] = Field(default=None, ge=0)
    # Saldo real de la PPD si la sync lo conoce; sin él, una TIMBRADA PPD
    # arranca con saldo = total (igual que una nativa recién timbrada).
    saldo_insoluto: Optional[Decimal] = Field(default=None, ge=0)
    lineas: List[LineaFacturaEspejoIn] = Field(default_factory=list)


class LineaFacturaOut(ORMModel):
    numero_linea: int
    # Nulo SOLO en líneas de facturas espejo cuya clave SAE no cruzó con el
    # catálogo (la descripción viaja igual).
    producto_id: Optional[uuid.UUID] = None
    clave_prod_serv: str
    clave_unidad: str
    descripcion: str
    cantidad: Decimal
    valor_unitario: Decimal
    importe: Decimal
    descuento: Decimal
    objeto_imp: str
    iva_tasa: Decimal
    iva_importe: Decimal
    ieps_tipo: Optional[str] = None
    ieps_valor: Decimal
    ieps_importe: Decimal
    ret_iva_importe: Decimal
    ret_isr_importe: Decimal


class FacturaOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    serie: str
    folio: int
    cliente_id: uuid.UUID
    # nueva → vieja: la factura previa que ESTA sustituye (relación CFDI "04").
    # Debe ir ANTES del campo `uuid` de abajo (ese sombrea el módulo uuid).
    sustituye_a_factura_id: Optional[uuid.UUID] = None
    uso_cfdi: str
    forma_pago: str
    metodo_pago: str
    moneda: str
    tipo_comprobante: str
    lugar_expedicion: Optional[str] = None
    fecha: datetime
    subtotal: Decimal
    descuento: Decimal
    iva_trasladado: Decimal
    ieps_trasladado: Decimal
    ret_iva: Decimal
    ret_isr: Decimal
    total: Decimal
    saldo_insoluto: Decimal = Decimal("0")
    # 'NATIVA' | 'ESPEJO_SAE' — el espejo se badgea en la lista y tiene
    # candados (nunca PAC).
    origen: str = "NATIVA"
    estado: str
    uuid: Optional[str] = None
    fecha_timbrado: Optional[datetime] = None
    fecha_cancelacion: Optional[datetime] = None
    motivo_cancelacion: Optional[str] = None
    # Sustitución CFDI: vieja → nueva (UUID de quien la sustituye, al cancelar 01).
    # El sentido nueva → vieja (sustituye_a_factura_id) se declara arriba, antes del
    # campo `uuid` que sombrearía el módulo uuid en el cuerpo de la clase.
    uuid_sustitucion: Optional[str] = None
    pdf_url: Optional[str] = None
    notas: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class FacturaDetailOut(FacturaOut):
    lineas: List[LineaFacturaOut] = []
