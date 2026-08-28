"""Cobranza — estado de cuenta del cliente (F1) y complementos de pago/REP (F2).

Estado de cuenta = Cuentas por Cobrar del cliente estilo SAE: sus facturas PPD
timbradas no saldadas (cargos), con antigüedad de saldos por FECHA DE
VENCIMIENTO (fecha de la factura + días de crédito del cliente) en intervalos de
30 días con una columna "Por vencer". Los abonos (REP) llegan en F2.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.db import set_role_tenant
from ...core.ratelimit import enforce
from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, Factura, ReciboPago, ReciboPagoFactura, Tenant, TimbradoIntento
from ...services.cfdi import emisor_rfc_esperado
from ...services.facturama import FacturamaClient, FacturamaError
from ...services.rep import build_payload_rep
from ...services.series import consumir_folio, resolver_serie, siguiente_folio
from ._helpers import get_or_404

router = APIRouter(prefix="/cobranza", tags=["cobranza"])

_READ = "menu:facturas"      # ver cobranza = ver facturas
_WRITE = "factura:gestionar"  # registrar/timbrar REP = mismo permiso que timbrar facturas
ZERO = Decimal("0")


def _bucket(dias_vencida: int) -> str:
    """Cubeta de antigüedad estilo SAE: por vencer + intervalos de 30 días."""
    if dias_vencida <= 0:
        return "por_vencer"
    if dias_vencida <= 30:
        return "d1_30"
    if dias_vencida <= 60:
        return "d31_60"
    if dias_vencida <= 90:
        return "d61_90"
    return "d90_mas"


@router.get("/estado-cuenta/{cliente_id}")
def estado_cuenta(
    cliente_id: UUID,
    corte: date | None = Query(default=None, description="Fecha de corte (default hoy)"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Estado de cuenta de un cliente: sus facturas PPD timbradas con saldo
    pendiente + antigüedad de saldos por fecha de vencimiento."""
    cliente = get_or_404(db, Cliente, cliente_id)
    hoy = corte or datetime.now(timezone.utc).date()
    dias_credito = int(cliente.dias_credito or 0)

    facturas = (
        db.query(Factura)
        .filter(
            Factura.deleted_at.is_(None),
            Factura.cliente_id == cliente_id,
            Factura.estado == "TIMBRADA",
            Factura.metodo_pago == "PPD",
            Factura.saldo_insoluto > 0,
        )
        .order_by(Factura.fecha.asc())
        .all()
    )

    antiguedad = {"por_vencer": Decimal("0"), "d1_30": Decimal("0"),
                  "d31_60": Decimal("0"), "d61_90": Decimal("0"), "d90_mas": Decimal("0")}
    docs = []
    saldo_total = Decimal("0")
    for f in facturas:
        f_fecha = f.fecha.date() if isinstance(f.fecha, datetime) else f.fecha
        vencimiento = f_fecha + timedelta(days=dias_credito)
        dias_vencida = (hoy - vencimiento).days
        saldo = Decimal(f.saldo_insoluto)
        saldo_total += saldo
        antiguedad[_bucket(dias_vencida)] += saldo
        docs.append({
            "factura_id": str(f.id),
            "serie": f.serie,
            "folio": f.folio,
            "uuid": f.uuid,
            "fecha": f_fecha,
            "vencimiento": vencimiento,
            "dias_vencida": dias_vencida,
            "total": f.total,
            "saldo_insoluto": saldo,
        })

    return {
        "cliente_id": str(cliente.id),
        "cliente_nombre": cliente.legal_name,
        "dias_credito": dias_credito,
        "limite_credito": cliente.limite_credito,
        "corte": hoy,
        "saldo_total": saldo_total,
        "antiguedad": antiguedad,
        "facturas": docs,
    }


# ─── Recibos de Pago (REP) — registrar + timbrar ─────────────────────────────
def _commit_seguro(db: Session, ctx: AuthContext) -> None:
    """Commit intermedio re-armando ROLE app_user + GUC del tenant (mismo patrón
    que facturas): un timbre del SAT jamás se pierde por un rollback posterior."""
    db.commit()
    set_role_tenant(db, ctx.tenant_id)


def _next_folio_rep(db: Session, ctx: AuthContext, cliente_id) -> tuple[str, int]:
    """Serie/folio del REP: serie tipo PAGO resuelta (override→sucursal→cliente→
    default), contador atómico; fallback a serie 'P' por código."""
    serie = resolver_serie(db, ctx.tenant_id, "PAGO", cliente_id=cliente_id)
    if serie is not None:
        folio = consumir_folio(db, serie.id)
        if folio is not None:
            return serie.codigo, folio
    folio = siguiente_folio(db, ctx.tenant_id, codigo="P", tipo_documento="PAGO")
    if folio is not None:
        return "P", folio
    mx = db.query(func.coalesce(func.max(ReciboPago.folio), 0)).filter(ReciboPago.serie == "P").scalar()
    return "P", int(mx) + 1


class ReciboFacturaIn(BaseModel):
    factura_id: UUID
    importe: Decimal = Field(gt=0)


class ReciboPagoIn(BaseModel):
    cliente_id: UUID
    fecha_pago: datetime
    forma_pago: str = Field(max_length=5)          # SAT: 01/03/04/...
    monto: Decimal = Field(gt=0)
    moneda: str = Field(default="MXN", max_length=3)
    num_operacion: Optional[str] = Field(default=None, max_length=100)
    banco: Optional[str] = Field(default=None, max_length=100)
    notas: Optional[str] = Field(default=None, max_length=500)
    facturas: list[ReciboFacturaIn] = Field(min_length=1, max_length=100)


def _recibo_out(db: Session, recibo: ReciboPago) -> dict:
    filas = (
        db.query(ReciboPagoFactura)
        .filter(ReciboPagoFactura.recibo_id == recibo.id)
        .all()
    )
    fac = {
        f.id: f for f in db.query(Factura).filter(
            Factura.id.in_([r.factura_id for r in filas])
        ).all()
    } if filas else {}
    return {
        "id": str(recibo.id),
        "serie": recibo.serie, "folio": recibo.folio,
        "cliente_id": str(recibo.cliente_id),
        "fecha_pago": recibo.fecha_pago,
        "forma_pago": recibo.forma_pago,
        "monto": recibo.monto, "moneda": recibo.moneda,
        "num_operacion": recibo.num_operacion, "banco": recibo.banco,
        "estado": recibo.estado, "uuid": recibo.uuid,
        "fecha_timbrado": recibo.fecha_timbrado,
        "facturas": [{
            "factura_id": str(r.factura_id),
            "serie": fac[r.factura_id].serie if r.factura_id in fac else None,
            "folio": fac[r.factura_id].folio if r.factura_id in fac else None,
            "importe_pagado": r.importe_pagado,
            "num_parcialidad": r.num_parcialidad,
            "saldo_anterior": r.saldo_anterior,
            "saldo_insoluto": r.saldo_insoluto,
        } for r in filas],
    }


@router.get("/recibos-pago")
def list_recibos(
    cliente_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    q = db.query(ReciboPago).filter(ReciboPago.tenant_id == ctx.tenant_id)
    if cliente_id is not None:
        q = q.filter(ReciboPago.cliente_id == cliente_id)
    total = q.count()
    rows = q.order_by(ReciboPago.created_at.desc()).limit(limit).offset(offset).all()
    return {"items": [_recibo_out(db, r) for r in rows], "total": total,
            "limit": limit, "offset": offset}


@router.get("/recibos-pago/{recibo_id}")
def get_recibo(
    recibo_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return _recibo_out(db, get_or_404(db, ReciboPago, recibo_id, soft=False))


@router.post("/recibos-pago", status_code=201)
def crear_recibo(
    payload: ReciboPagoIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Registra un pago (BORRADOR) y le anexa las facturas que cubre. Valida que
    todas sean PPD timbradas del cliente, que cada importe no exceda su saldo, y
    que la suma cuadre con el monto. Calcula parcialidad y saldos por factura."""
    cliente = get_or_404(db, Cliente, payload.cliente_id)
    suma = sum((Decimal(f.importe) for f in payload.facturas), ZERO)
    if abs(suma - Decimal(payload.monto)) > Decimal("0.01"):
        raise HTTPException(status_code=422, detail=f"La suma aplicada (${suma:,.2f}) no cuadra con el monto (${payload.monto:,.2f})")

    recibo = ReciboPago(
        tenant_id=ctx.tenant_id, cliente_id=cliente.id,
        fecha_pago=payload.fecha_pago, forma_pago=payload.forma_pago,
        monto=Decimal(payload.monto), moneda=payload.moneda,
        num_operacion=payload.num_operacion, banco=payload.banco,
        notas=(payload.notas or "").strip() or None,
        estado="BORRADOR", created_by=ctx.user_id,
    )
    serie, folio = _next_folio_rep(db, ctx, cliente.id)
    recibo.serie, recibo.folio = serie, folio
    db.add(recibo)
    db.flush()

    for item in payload.facturas:
        f = (
            db.query(Factura)
            .filter(Factura.id == item.factura_id, Factura.deleted_at.is_(None))
            .with_for_update()
            .one_or_none()
        )
        if f is None or f.cliente_id != cliente.id:
            raise HTTPException(status_code=422, detail="Una factura no pertenece al cliente")
        if f.origen == "ESPEJO_SAE":
            # Su REP lo emite SAE (serie ZCP); registrarle un recibo aquí y
            # timbrarlo sería un segundo complemento de pago ante el SAT. Los
            # abonos de las facturas espejo llegan por la sync del conector.
            raise HTTPException(
                status_code=422,
                detail=f"{f.serie}{f.folio} es espejo de SAE: su pago se registra en SAE "
                       "y el espejo lo refleja",
            )
        if f.estado != "TIMBRADA" or f.metodo_pago != "PPD":
            raise HTTPException(status_code=422, detail=f"La factura {f.serie}{f.folio} no es una PPD timbrada")
        saldo_ant = Decimal(f.saldo_insoluto)
        importe = Decimal(item.importe)
        if importe > saldo_ant + Decimal("0.01"):
            raise HTTPException(
                status_code=422,
                detail=f"El importe (${importe:,.2f}) excede el saldo de {f.serie}{f.folio} (${saldo_ant:,.2f})",
            )
        # Parcialidad = cuántos abonos previos (recibos timbrados) tiene la factura + 1.
        previos = (
            db.query(func.count(ReciboPagoFactura.id))
            .join(ReciboPago, ReciboPago.id == ReciboPagoFactura.recibo_id)
            .filter(ReciboPagoFactura.factura_id == f.id, ReciboPago.estado == "TIMBRADO")
            .scalar()
        )
        db.add(ReciboPagoFactura(
            tenant_id=ctx.tenant_id, recibo_id=recibo.id, factura_id=f.id,
            importe_pagado=importe, num_parcialidad=int(previos) + 1,
            saldo_anterior=saldo_ant, saldo_insoluto=saldo_ant - importe,
            moneda_dr=f.moneda or "MXN",
        ))
    db.flush()
    db.refresh(recibo)
    return _recibo_out(db, recibo)


@router.post("/recibos-pago/{recibo_id}/timbrar")
def timbrar_recibo(
    recibo_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Timbra el REP ante el PAC y descuenta el saldo insoluto de cada factura.
    Mismo blindaje que las facturas: FOR UPDATE, bitácora anti-doble-timbrado,
    persistencia inmediata del timbre."""
    enforce(f"timbrar-rep:{ctx.tenant_id}", 300, 3600)
    recibo = get_or_404(db, ReciboPago, recibo_id, soft=False, for_update=True)
    if recibo.estado == "TIMBRADO":
        raise HTTPException(status_code=409, detail="El recibo ya está timbrado")
    if recibo.estado == "CANCELADO":
        raise HTTPException(status_code=409, detail="El recibo está cancelado")

    client = FacturamaClient.from_settings(settings)
    if not client.configured:
        raise HTTPException(status_code=503, detail="Facturama no está configurado")

    filas = db.query(ReciboPagoFactura).filter(ReciboPagoFactura.recibo_id == recibo.id).all()
    docs = []
    for rf in filas:
        f = db.query(Factura).filter(Factura.id == rf.factura_id).with_for_update().one()
        # Revalidar el saldo: pudo cambiar desde que se registró el borrador.
        if Decimal(rf.importe_pagado) > Decimal(f.saldo_insoluto) + Decimal("0.01"):
            raise HTTPException(
                status_code=422,
                detail=f"El saldo de {f.serie}{f.folio} cambió; recrea el recibo",
            )
        docs.append((rf, f))

    cliente = db.query(Cliente).filter(Cliente.id == recibo.cliente_id).one()
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()

    # Bitácora anti-doble-timbrado (igual que facturas): PENDIENTE fresco = mutex;
    # viejo = reconcilia contra el PAC por serie/folio antes de re-timbrar.
    pendientes = (
        db.query(TimbradoIntento)
        .filter(TimbradoIntento.recibo_pago_id == recibo.id, TimbradoIntento.estado == "PENDIENTE")
        .all()
    )
    if pendientes:
        ahora = db.query(func.now()).scalar()
        if any((ahora - i.created_at).total_seconds() < 300 for i in pendientes):
            raise HTTPException(status_code=409, detail="Hay un timbrado en curso para este recibo; espera y recarga")
        # Reconciliación por OrderNumber (= serie+folio del recibo). El CFDI de pago
        # (tipo P) tiene Total 0, así que aquí `total` no aplica: acota por RFC del
        # receptor + emisor (multi-emisor) + fecha y confirma el OrderNumber exacto.
        desde = min(i.created_at for i in pendientes) - timedelta(days=1)
        ok, existente = client.buscar_cfdi(
            f"{recibo.serie}{recibo.folio}",
            receptor_rfc=cliente.rfc,
            emisor_rfc=emisor_rfc_esperado(settings, tenant),
            desde=desde,
        )
        if not ok:
            raise HTTPException(status_code=409, detail="Intento previo sin confirmar y no se pudo verificar con el PAC; reintenta")
        if existente is not None:
            recibo.facturama_id = existente.get("Id")
            recibo.uuid = (((existente.get("Complement") or {}).get("TaxStamp") or {}).get("Uuid")
                           or existente.get("Uuid"))
            recibo.estado = "TIMBRADO"
            recibo.fecha_timbrado = func.now()
            _aplicar_saldos(docs)
            for i in pendientes:
                i.estado = "TIMBRADA"; i.detalle = "Reconciliado: el CFDI ya existía"
            _commit_seguro(db, ctx)
            db.refresh(recibo)
            return _recibo_out(db, recibo)
        for i in pendientes:
            i.estado = "ERROR"; i.detalle = "Verificado con el PAC: no llegó a timbrar"

    payload = build_payload_rep(recibo, cliente, tenant, docs, settings)
    intento = TimbradoIntento(tenant_id=ctx.tenant_id, recibo_pago_id=recibo.id, estado="PENDIENTE")
    db.add(intento)
    _commit_seguro(db, ctx)

    try:
        resp = client.create_cfdi_pago(payload)
    except FacturamaError as exc:
        intento.estado = "ERROR"; intento.detalle = str(exc)[:2000]
        _commit_seguro(db, ctx)
        raise HTTPException(status_code=502, detail=f"Timbrado del REP rechazado por el PAC: {exc}")

    recibo.facturama_id = resp.get("Id")
    recibo.uuid = ((resp.get("Complement") or {}).get("TaxStamp") or {}).get("Uuid") or resp.get("Uuid")
    recibo.estado = "TIMBRADO"
    recibo.fecha_timbrado = func.now()
    intento.estado = "TIMBRADA"
    _aplicar_saldos(docs)                 # descuenta el saldo insoluto de cada factura
    _commit_seguro(db, ctx)               # el timbre se persiste en el instante
    try:
        recibo.xml = client.download_xml(recibo.facturama_id).decode("utf-8", "ignore")
        db.flush()
    except FacturamaError:
        pass                               # el timbre ya quedó; el XML se baja luego
    db.refresh(recibo)
    return _recibo_out(db, recibo)


def _aplicar_saldos(docs) -> None:
    """Baja el saldo insoluto de cada factura al saldo calculado en el recibo."""
    for rf, factura in docs:
        factura.saldo_insoluto = Decimal(rf.saldo_insoluto)


# ─── REP: cancelar / PDF / XML / enviar (F3) ─────────────────────────────────
class CancelarReciboIn(BaseModel):
    # 02 sin relación | 01 con sustitución (requiere uuid) | 03 | 04
    motivo: str = Field(default="02", pattern="^0[1-4]$")
    uuid_sustitucion: Optional[UUID] = None


def _docs_recibo(db: Session, recibo: ReciboPago, *, lock: bool = False):
    filas = db.query(ReciboPagoFactura).filter(ReciboPagoFactura.recibo_id == recibo.id).all()
    out = []
    for rf in filas:
        q = db.query(Factura).filter(Factura.id == rf.factura_id)
        if lock:
            q = q.with_for_update()
        out.append((rf, q.one()))
    return out


@router.post("/recibos-pago/{recibo_id}/cancelar")
def cancelar_recibo(
    recibo_id: UUID,
    payload: CancelarReciboIn = Body(default=CancelarReciboIn()),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Cancela el REP ante el PAC y REVIERTE el saldo insoluto de cada factura
    (le regresa el importe abonado). El SAT exige aceptación del receptor para
    cancelar un CFDI de pago (positiva ficta a 3 días)."""
    recibo = get_or_404(db, ReciboPago, recibo_id, soft=False, for_update=True)
    if recibo.estado != "TIMBRADO":
        raise HTTPException(status_code=409, detail="Solo se cancela un recibo timbrado")
    if payload.motivo == "01" and payload.uuid_sustitucion is None:
        raise HTTPException(status_code=422, detail="El motivo 01 requiere uuid_sustitucion")

    if not settings.FACTURAMA_FAKE_CANCEL:
        client = FacturamaClient.from_settings(settings)
        if not client.configured:
            raise HTTPException(status_code=503, detail="Facturama no está configurado")
        try:
            client.cancel_cfdi(
                recibo.facturama_id, motive=payload.motivo,
                uuid_replacement=str(payload.uuid_sustitucion) if payload.uuid_sustitucion else None,
            )
        except FacturamaError as exc:
            raise HTTPException(status_code=502, detail=f"Cancelación del REP rechazada por el PAC: {exc}")

    # Revierte el abono: el saldo insoluto de cada factura sube por el importe
    # pagado (tope = total, por si acaso).
    for rf, factura in _docs_recibo(db, recibo, lock=True):
        nuevo = Decimal(factura.saldo_insoluto) + Decimal(rf.importe_pagado)
        factura.saldo_insoluto = nuevo if nuevo <= Decimal(factura.total) else Decimal(factura.total)

    recibo.estado = "CANCELADO"
    recibo.fecha_cancelacion = func.now()
    recibo.motivo_cancelacion = payload.motivo
    db.flush()
    db.refresh(recibo)
    return _recibo_out(db, recibo)


def _recibo_pdf_bytes(db: Session, ctx: AuthContext, recibo: ReciboPago) -> bytes:
    from ...services.recibo_pdf import build_recibo_pdf
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    cliente = db.query(Cliente).filter(Cliente.id == recibo.cliente_id).one()
    return build_recibo_pdf(recibo, tenant, cliente, _docs_recibo(db, recibo))


@router.get("/recibos-pago/{recibo_id}/pdf")
def recibo_pdf(
    recibo_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    from fastapi import Response
    recibo = get_or_404(db, ReciboPago, recibo_id, soft=False)
    pdf = _recibo_pdf_bytes(db, ctx, recibo)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="REP-{recibo.serie}{recibo.folio}.pdf"'})


@router.get("/recibos-pago/{recibo_id}/xml")
def recibo_xml(
    recibo_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    from fastapi import Response
    recibo = get_or_404(db, ReciboPago, recibo_id, soft=False)
    if not recibo.xml:
        raise HTTPException(status_code=404, detail="El recibo no tiene XML (¿no está timbrado?)")
    return Response(content=recibo.xml, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="REP-{recibo.serie}{recibo.folio}.xml"'})


class EnviarReciboIn(BaseModel):
    to: list[str] = Field(min_length=1, max_length=20)
    mensaje: Optional[str] = Field(default=None, max_length=2000)


@router.post("/recibos-pago/{recibo_id}/enviar")
def enviar_recibo(
    recibo_id: UUID,
    payload: EnviarReciboIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Envía el PDF + XML del REP por correo (SMTP del tenant)."""
    import html as html_mod

    from ...services import email as email_service
    from .remisiones import _validar_destinatarios

    recibo = get_or_404(db, ReciboPago, recibo_id, soft=False)
    if recibo.estado != "TIMBRADO":
        raise HTTPException(status_code=409, detail="Solo se envía un recibo timbrado")
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    if not email_service.configured(tenant):
        raise HTTPException(status_code=503, detail="Configura una cuenta de correo en Ajustes › Correo")
    destinatarios = _validar_destinatarios(payload.to)

    pdf = _recibo_pdf_bytes(db, ctx, recibo)
    nombre = f"REP-{recibo.serie}{recibo.folio}"
    attachments: list[tuple[str, bytes, str]] = [(f"{nombre}.pdf", pdf, "application/pdf")]
    if recibo.xml:
        attachments.append((f"{nombre}.xml", recibo.xml.encode("utf-8"), "application/xml"))
    mensaje_html = f"<p>{html_mod.escape(payload.mensaje)}</p>" if payload.mensaje else ""
    html = (
        f"{mensaje_html}"
        f"<p>Adjunto el recibo electrónico de pago <strong>{html_mod.escape(nombre)}</strong>"
        f" por <strong>${recibo.monto:,.2f} {recibo.moneda}</strong>.</p>"
        f"<p>UUID: <span style=\"font-family:monospace\">{html_mod.escape(recibo.uuid or '')}</span></p>"
    )
    try:
        email_service.send_email(email_service.smtp_config(tenant), destinatarios,
                                 f"Recibo de pago {nombre}", html, attachments=attachments)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "to": ", ".join(destinatarios)}
