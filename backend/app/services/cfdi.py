"""Construye el payload CFDI 4.0 (Facturama /3/cfdis) desde una Factura v2.

Las líneas ya traen el desglose fiscal calculado al crear la factura
(services/fiscal.py), así que aquí solo se mapea al formato de Facturama.
El emisor (Issuer) se omite si no hay FACTURAMA_ISSUER_RFC configurado: en ese
caso Facturama usa el CSD por defecto de la cuenta (correcto cuando la cuenta
tiene un único CSD; en producción conviene fijar FACTURAMA_ISSUER_RFC al RFC real
del emisor cuyo CSD está cargado en Facturama).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..core.config import settings

_MX_TZ = ZoneInfo("America/Mexico_City")
from ..models import Cliente, Factura, Producto, ProductoCliente, Tenant


def _f(x) -> float:
    return float(Decimal(str(x or 0)))


def emisor_rfc_esperado(settings, tenant) -> str | None:
    """RFC del emisor con que se timbra, según la MISMA precedencia que arma el
    Issuer del payload: override global (FACTURAMA_ISSUER_RFC) → multi-emisor
    (RFC del propio tenant) → None (CSD por defecto de la cuenta = emisor único).

    Lo usa la reconciliación (buscar_cfdi) para, en cuentas multi-emisor, no
    adoptar un CFDI emitido por OTRO tenant que comparte la misma cuenta de
    Facturama. En modo emisor-único devuelve None y no hace falta filtrar."""
    if getattr(settings, "FACTURAMA_ISSUER_RFC", ""):
        return settings.FACTURAMA_ISSUER_RFC
    if getattr(settings, "FACTURAMA_MULTIEMISOR", False):
        return getattr(tenant, "rfc", None)
    return None


ZERO = Decimal("0")
_RFC_PUBLICO = "XAXX010101000"


def _receptor_cp(cliente: Cliente, tenant: Tenant) -> str:
    dom = cliente.domicilio_fiscal or {}
    return str(dom.get("cp") or dom.get("codigo_postal") or tenant.domicilio_fiscal_cp or "")


def _receptor(factura: Factura, cliente: Cliente, tenant: Tenant, expedition: str) -> dict:
    """Bloque Receiver. Público en general (XAXX) exige nombre/régimen/uso fijos
    (PUBLICO EN GENERAL · 616 · S01) y su CP debe igualar el lugar de expedición."""
    cp = _receptor_cp(cliente, tenant)
    if cliente.rfc == _RFC_PUBLICO:
        return {
            "Rfc": _RFC_PUBLICO,
            "Name": "PUBLICO EN GENERAL",
            "CfdiUse": "S01",
            "FiscalRegime": "616",
            "TaxZipCode": expedition,  # regla CFDI: debe ser igual a ExpeditionPlace
        }
    return {
        "Rfc": cliente.rfc,
        "Name": cliente.legal_name,
        "CfdiUse": factura.uso_cfdi or cliente.uso_cfdi_default or "G03",
        "FiscalRegime": cliente.regimen_fiscal or "616",
        "TaxZipCode": cp,
    }


def build_payload(db: Session, factura: Factura) -> dict:
    cliente = db.query(Cliente).filter(Cliente.id == factura.cliente_id).one()
    tenant = db.query(Tenant).filter(Tenant.id == factura.tenant_id).one()
    # Lugar de expedición = CP del emisor. En multi-emisor es el del tenant; el env
    # solo es respaldo. En modo single-emisor se conserva la precedencia previa.
    if getattr(settings, "FACTURAMA_MULTIEMISOR", False):
        expedition = factura.lugar_expedicion or tenant.domicilio_fiscal_cp or settings.FACTURAMA_EXPEDITION_PLACE
    else:
        expedition = settings.FACTURAMA_EXPEDITION_PLACE or factura.lugar_expedicion or tenant.domicilio_fiscal_cp

    # Productos (para los litros del IEPS por cuota y el SKU de NoIdentificacion).
    prod_ids = {ln.producto_id for ln in factura.lineas if ln.producto_id}
    productos = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(prod_ids)).all()}

    # Catálogo por cliente: cómo llama ESTE cliente a cada producto. Estándar
    # de línea: Description = nombre del cliente (si lo definió) o el interno;
    # IdentificationNumber (NoIdentificacion) = código del cliente o el SKU —
    # siempre viaja, así todos los CFDI llevan una clave rastreable sin duplicar
    # productos por cliente.
    # Con claves por plaza (producto_clientes.sucursal_id), la GENÉRICA manda
    # en el CFDI nativo: se ordena para que pise a las scoped en el dict. Las
    # claves por plaza existen para el masivo de SAE (cada plaza exporta a su
    # empresa); la factura nativa no carga plaza por línea hoy.
    alias_cliente = {
        pc.producto_id: pc
        for pc in db.query(ProductoCliente)
        .filter(
            ProductoCliente.cliente_id == factura.cliente_id,
            ProductoCliente.producto_id.in_(prod_ids),
        )
        # Y desempate por sucursal_id: si solo hay filas scoped (sin genérica),
        # que al menos gane SIEMPRE la misma, no la que el heap devuelva hoy.
        .order_by(
            ProductoCliente.sucursal_id.is_(None).asc(),
            ProductoCliente.sucursal_id.asc(),
        )
        .all()
    } if prod_ids else {}

    items = []
    for ln in sorted(factura.lineas, key=lambda x: x.numero_linea):
        taxes = []
        retenciones = []
        if str(ln.objeto_imp) == "02":
            ieps_imp = Decimal(str(ln.ieps_importe or 0))
            # El IEPS se calcula ANTES del IVA → la base del IVA es importe + IEPS
            # (regla CFDI). Así Base×Rate = Total que exige el PAC.
            iva_base = Decimal(str(ln.importe)) + ieps_imp
            taxes.append({
                "Total": _f(ln.iva_importe), "Name": "IVA", "Base": _f(iva_base),
                "Rate": _f(ln.iva_tasa), "IsRetention": False, "IsQuota": False,
            })
            if ieps_imp > 0:
                # IEPS por CUOTA (TipoFactor Cuota): Base = unidades gravadas (litros),
                # Rate = cuota → Base×Rate = importe IEPS. Por TASA: Base = importe, Rate = tasa.
                es_cuota = str(ln.ieps_tipo) == "CUOTA"
                if es_cuota:
                    prod = productos.get(ln.producto_id)
                    litros = Decimal(str(prod.contenido_litros)) if (prod and prod.contenido_litros) else ZERO
                    ieps_base = (Decimal(str(ln.cantidad)) * litros) if litros else (
                        ieps_imp / Decimal(str(ln.ieps_valor)) if ln.ieps_valor else ZERO)
                else:
                    ieps_base = Decimal(str(ln.importe))
                taxes.append({
                    "Total": _f(ln.ieps_importe), "Name": "IEPS", "Base": _f(ieps_base),
                    "Rate": _f(ln.ieps_valor), "IsRetention": False, "IsQuota": es_cuota,
                })
            if ln.ret_iva_importe and Decimal(ln.ret_iva_importe) > 0:
                retenciones.append({
                    "Total": _f(ln.ret_iva_importe), "Name": "IVA", "Base": _f(ln.importe),
                    "Rate": _f(ln.iva_tasa), "IsRetention": True, "IsQuota": False,
                })
            if ln.ret_isr_importe and Decimal(ln.ret_isr_importe) > 0:
                retenciones.append({
                    "Total": _f(ln.ret_isr_importe), "Name": "ISR", "Base": _f(ln.importe),
                    "Rate": 0, "IsRetention": True, "IsQuota": False,
                })

        pc = alias_cliente.get(ln.producto_id)
        prod_ln = productos.get(ln.producto_id)
        descripcion = (pc.nombre_cliente or "").strip() if pc else ""
        no_ident = (pc.codigo_cliente or "").strip() if pc else ""
        item = {
            "ProductCode": ln.clave_prod_serv,
            "Description": descripcion or ln.descripcion,
            "UnitCode": ln.clave_unidad,
            "Unit": ln.clave_unidad,
            "UnitPrice": _f(ln.valor_unitario),
            "Quantity": _f(ln.cantidad),
            "Subtotal": _f(ln.importe),
            "Discount": _f(ln.descuento),
            "TaxObject": ln.objeto_imp,
            "Total": _f(Decimal(str(ln.importe)) + Decimal(str(ln.iva_importe or 0)) + Decimal(str(ln.ieps_importe or 0))
                       - Decimal(str(ln.ret_iva_importe or 0)) - Decimal(str(ln.ret_isr_importe or 0))),
        }
        if no_ident or (prod_ln and prod_ln.sku):
            item["IdentificationNumber"] = no_ident or prod_ln.sku
        if taxes or retenciones:
            item["Taxes"] = taxes + retenciones
        items.append(item)

    payload = {
        "NameId": "1",                       # CFDI ingresos
        "CfdiType": "I",
        # Sin este campo, Facturama usaba su propio default y el timbrado por
        # API (a diferencia de crear la factura a mano en su portal, que sí
        # lo manda) quedaba rechazado con un error de "Nombre del emisor" que
        # en realidad no era por el nombre. CFDI exige hora LOCAL del lugar
        # de expedición (México), sin sufijo de zona horaria.
        "Date": datetime.now(_MX_TZ).strftime("%Y-%m-%dT%H:%M:%S"),
        "PaymentForm": factura.forma_pago or "99",
        "PaymentMethod": factura.metodo_pago or "PUE",
        "Currency": factura.moneda or "MXN",
        "ExpeditionPlace": expedition,
        "Receiver": _receptor(factura, cliente, tenant, expedition),
        "Items": items,
    }

    # Serie/Folio: Facturama SOLO acepta series registradas en la cuenta/sucursal.
    # v2 maneja su propia serie/folio internamente (no se sobreescriben al timbrar),
    # así que por defecto NO se envían al PAC y Facturama asigna su folio. Enviar una
    # serie no registrada (p. ej. "SLP") provoca 400 "El atributo 'Serie' debe existir
    # en la sucursal". Si la cuenta tiene sus series dadas de alta, activar
    # FACTURAMA_SEND_SERIE para enviarlas.
    if getattr(settings, "FACTURAMA_SEND_SERIE", False) and factura.serie:
        payload["Serie"] = factura.serie
        payload["Folio"] = factura.folio

    # Identificador propio en OrderNumber (= serie+folio de la app). Es el ancla
    # para reconciliar un intento de timbrado que murió a media llamada (crash
    # entre create_cfdi y el commit): al no enviar Serie/Folio por defecto,
    # Facturama asigna su propio folio y no había forma de re-encontrar el CFDI.
    # OrderNumber NO se indexa en el `keyword` de Facturama, pero SÍ vuelve en el
    # detalle del CFDI, así que buscar_cfdi acota por receptor/emisor y confirma
    # este valor exacto (único por documento → cero adopciones equivocadas).
    # Se imprime como "Orden de Compra" en el PDF: es la referencia interna.
    if factura.serie and str(factura.folio or "").strip():
        payload["OrderNumber"] = f"{factura.serie}{factura.folio}"

    # Sustitución (refacturación): esta factura NUEVA reemplaza a una VIEJA. Se
    # reporta al SAT el nodo Relations TipoRelacion "04" (Sustitución de los CFDI
    # previos) con el UUID de la vieja. Solo si la vieja ya está timbrada (tiene
    # UUID); se omite si falta, igual que Serie/Folio (una relación sin UUID rompe
    # el patrón del PAC).
    if factura.sustituye_a_factura_id:
        vieja = (
            db.query(Factura)
            .filter(Factura.id == factura.sustituye_a_factura_id)
            .one_or_none()
        )
        if vieja is not None and (vieja.uuid or "").strip():
            payload["Relations"] = {"Type": "04", "Cfdis": [{"Uuid": vieja.uuid}]}

    # Público en general = factura global: requiere Información Global (periodicidad/mes/año).
    if cliente.rfc == _RFC_PUBLICO:
        payload["GlobalInformation"] = {
            "Periodicity": "04",                       # 04 = mensual
            "Months": f"{factura.fecha.month:02d}",
            "Year": factura.fecha.year,
        }

    # Emisor del CFDI. Precedencia:
    #   1. FACTURAMA_ISSUER_RFC → override GLOBAL de un solo emisor (sandbox/single).
    #   2. FACTURAMA_MULTIEMISOR=true → emisor = datos fiscales del PROPIO tenant
    #      (su RFC/CSD, ya subido a Facturama vía Ajustes › Empresa). Es el modo
    #      multi-empresa: cada quien factura a su nombre.
    #   3. ninguno → se omite y Facturama usa el CSD por defecto de la cuenta.
    if settings.FACTURAMA_ISSUER_RFC:
        payload["Issuer"] = {
            "Rfc": settings.FACTURAMA_ISSUER_RFC,
            "Name": settings.FACTURAMA_ISSUER_NAME or tenant.legal_name,
            "FiscalRegime": settings.FACTURAMA_ISSUER_REGIMEN or tenant.regimen_fiscal_sat,
        }
    elif getattr(settings, "FACTURAMA_MULTIEMISOR", False):
        payload["Issuer"] = {
            "Rfc": tenant.rfc,
            "Name": tenant.legal_name,
            "FiscalRegime": tenant.regimen_fiscal_sat,
        }
    return payload
