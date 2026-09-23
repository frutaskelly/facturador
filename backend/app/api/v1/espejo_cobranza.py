"""Espejo de cobranza: los REP y las notas de crédito que emite SAE.

Mismo contrato que el espejo de facturas (`POST /facturas/espejo`): los
deposita el conector con la clave `factura:espejo`, son idempotentes por el
documento de SAE —(empresa, CVE_DOC)— y re-mandarlos ACTUALIZA el reflejo, que
es como llegan las cancelaciones.

Lo que NO hacen, a propósito: mover saldos. El saldo de cada factura espejo ya
llega calculado desde la CxC de SAE (total − abonos, y las notas de crédito son
abonos ahí), así que descontarlo otra vez aquí lo contaría dos veces. Estas
tablas son para VER qué se pagó o se acreditó, cuándo y contra qué factura.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import (
    Cliente, Factura, NotaCredito, NotaCreditoFactura, ReciboPago, ReciboPagoFactura,
)

router = APIRouter(prefix="/cobranza/espejo", tags=["cobranza"])

_PERM = "factura:espejo"


class DoctoEspejoIn(BaseModel):
    """Un renglón del documento: la factura a la que abona y cuánto."""
    serie: str = Field(max_length=20)
    folio: int = Field(gt=0)
    importe: Decimal = Field(ge=0)
    # Sólo los REP: la parcialidad y los saldos como los deja la CxC de SAE
    num_parcialidad: Optional[int] = Field(default=None, ge=1)
    saldo_anterior: Optional[Decimal] = None
    saldo_insoluto: Optional[Decimal] = None


class _DocumentoEspejoIn(BaseModel):
    empresa: str = Field(max_length=4)
    # La llave del documento en SAE (FACTGxx.CVE_DOC / CFDIxx.CVE_DOC), tal cual
    cve_doc: str = Field(min_length=1, max_length=30)
    serie: str = Field(default="", max_length=20)
    folio: int = Field(gt=0)
    cliente_sae: str = Field(max_length=10)
    uuid: Optional[str] = Field(default=None, max_length=36)
    fecha_cancelacion: Optional[datetime] = None
    facturas: list[DoctoEspejoIn] = Field(default_factory=list, max_length=500)


class ReciboPagoEspejoIn(_DocumentoEspejoIn):
    fecha_pago: datetime
    forma_pago: str = Field(default="03", max_length=5)
    estado: str = Field(pattern="^(TIMBRADO|CANCELADO)$")
    # Sin renglones (un REP cancelado: SAE los borra de la CxC) el monto no se
    # puede sumar; el conector lo manda si lo conoce.
    monto: Optional[Decimal] = Field(default=None, ge=0)


class NotaCreditoEspejoIn(_DocumentoEspejoIn):
    fecha: datetime
    estado: str = Field(pattern="^(VIGENTE|CANCELADA)$")
    total: Optional[Decimal] = Field(default=None, ge=0)


def _cliente(db: Session, ctx: AuthContext, empresa: str, cliente_sae: str) -> Cliente:
    """El cliente por su equivalencia SAE, con el mismo candado que las facturas."""
    from ...services.cliente_match import buscar_equivalencia

    clave = f"{empresa}:{cliente_sae}"
    equiv = buscar_equivalencia(db, ctx.tenant_id, "SAE", clave, solo_confirmadas=True)
    if equiv is None:
        raise HTTPException(status_code=422, detail=f"Sin equivalencia SAE para '{clave}'")
    cliente = db.query(Cliente).filter(
        Cliente.id == equiv.cliente_id, Cliente.deleted_at.is_(None)
    ).one_or_none()
    if cliente is None:
        raise HTTPException(status_code=422, detail=f"El cliente de la equivalencia '{clave}' ya no existe")
    if not cliente.espejo_sae:
        raise HTTPException(
            status_code=422, detail=f"{cliente.legal_name} no está marcado en espejo SAE")
    return cliente


def _norm_serie(serie: str) -> str:
    return re.sub(r"\s+", "", serie or "").upper()


def _facturas_por_ref(db: Session, ctx: AuthContext, empresa: str,
                      docs: list[DoctoEspejoIn]) -> dict[tuple[str, int], Factura]:
    """{(serie, folio): factura espejo}. Una sola consulta para todo el documento.

    Se busca la factura ESPEJO de esa empresa: los folios de SAE son
    consecutivos por empresa y un 'ZHGO 100' nativo no es el de SAE.
    """
    llaves = {(_norm_serie(d.serie), d.folio) for d in docs}
    if not llaves:
        return {}
    filas = db.query(Factura).filter(
        Factura.tenant_id == ctx.tenant_id,
        Factura.deleted_at.is_(None),
        Factura.origen == "ESPEJO_SAE",
        Factura.folio.in_({f for _, f in llaves}),
        Factura.serie.in_({s for s, _ in llaves}),
    ).all()
    out: dict[tuple[str, int], Factura] = {}
    for f in filas:
        if f.espejo_empresa not in (None, empresa):
            continue
        out[(f.serie, f.folio)] = f
    return out


def _ref(d: DoctoEspejoIn) -> str:
    return f"{_norm_serie(d.serie)}{d.folio}"


@router.post("/recibo-pago")
def recibo_pago_espejo(
    payload: ReciboPagoEspejoIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_PERM)),
):
    """Refleja un REP timbrado (o cancelado) en SAE."""
    if payload.estado == "TIMBRADO" and not (payload.uuid or "").strip():
        raise HTTPException(status_code=422, detail="Un REP TIMBRADO sin UUID no existe")
    cliente = _cliente(db, ctx, payload.empresa, payload.cliente_sae)
    cve_doc = payload.cve_doc.strip()

    recibo = db.query(ReciboPago).filter(
        ReciboPago.tenant_id == ctx.tenant_id,
        ReciboPago.origen == "ESPEJO_SAE",
        ReciboPago.espejo_empresa == payload.empresa,
        ReciboPago.espejo_cve_doc == cve_doc,
    ).with_for_update().one_or_none()
    if recibo is None:
        recibo = ReciboPago(
            tenant_id=ctx.tenant_id, origen="ESPEJO_SAE",
            espejo_empresa=payload.empresa, espejo_cve_doc=cve_doc,
            created_by=ctx.user_id,
        )
        db.add(recibo)
    elif recibo.estado == "CANCELADO" and payload.estado != "CANCELADO":
        # Igual que las facturas: una cancelación no se deshace sola.
        raise HTTPException(
            status_code=409, detail=f"El REP {cve_doc} ya está CANCELADO en el espejo")

    recibo.cliente_id = cliente.id
    recibo.serie = _norm_serie(payload.serie)
    recibo.folio = payload.folio
    recibo.fecha_pago = payload.fecha_pago
    recibo.forma_pago = payload.forma_pago or "03"
    recibo.estado = payload.estado
    recibo.uuid = (payload.uuid or "").strip() or recibo.uuid
    recibo.fecha_timbrado = recibo.fecha_timbrado or payload.fecha_pago
    if payload.estado == "CANCELADO":
        recibo.fecha_cancelacion = (payload.fecha_cancelacion or recibo.fecha_cancelacion
                                    or datetime.now(timezone.utc))
    suma = sum((d.importe for d in payload.facturas), Decimal("0"))
    if payload.facturas:
        recibo.monto = suma
    elif payload.monto is not None:
        recibo.monto = payload.monto
    elif recibo.monto is None:
        recibo.monto = Decimal("0")
    db.flush()

    # Los renglones se reemplazan sólo si llegan: un REP cancelado ya no los
    # tiene en SAE, y borrar los que se vieron en vida perdería el rastro.
    if payload.facturas:
        facturas = _facturas_por_ref(db, ctx, payload.empresa, payload.facturas)
        db.query(ReciboPagoFactura).filter(
            ReciboPagoFactura.recibo_id == recibo.id
        ).delete(synchronize_session=False)
        for d in payload.facturas:
            f = facturas.get((_norm_serie(d.serie), d.folio))
            db.add(ReciboPagoFactura(
                tenant_id=ctx.tenant_id, recibo_id=recibo.id,
                factura_id=f.id if f else None, factura_ref=_ref(d),
                importe_pagado=d.importe, num_parcialidad=d.num_parcialidad or 1,
                saldo_anterior=d.saldo_anterior, saldo_insoluto=d.saldo_insoluto,
                moneda_dr="MXN",
            ))
        db.flush()
    return {"id": str(recibo.id), "estado": recibo.estado, "monto": recibo.monto,
            "facturas": len(payload.facturas)}


@router.post("/nota-credito")
def nota_credito_espejo(
    payload: NotaCreditoEspejoIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_PERM)),
):
    """Refleja una nota de crédito (CFDI de egreso) de SAE con sus aplicaciones."""
    cliente = _cliente(db, ctx, payload.empresa, payload.cliente_sae)
    cve_doc = payload.cve_doc.strip()

    nota = db.query(NotaCredito).filter(
        NotaCredito.tenant_id == ctx.tenant_id,
        NotaCredito.espejo_empresa == payload.empresa,
        NotaCredito.espejo_cve_doc == cve_doc,
    ).with_for_update().one_or_none()
    if nota is None:
        nota = NotaCredito(
            tenant_id=ctx.tenant_id, origen="ESPEJO_SAE",
            espejo_empresa=payload.empresa, espejo_cve_doc=cve_doc,
        )
        db.add(nota)
    elif nota.estado == "CANCELADA" and payload.estado != "CANCELADA":
        raise HTTPException(
            status_code=409, detail=f"La nota {cve_doc} ya está CANCELADA en el espejo")

    nota.cliente_id = cliente.id
    nota.serie = _norm_serie(payload.serie)
    nota.folio = payload.folio
    nota.fecha = payload.fecha
    nota.estado = payload.estado
    nota.uuid = (payload.uuid or "").strip() or nota.uuid
    if payload.estado == "CANCELADA":
        nota.fecha_cancelacion = (payload.fecha_cancelacion or nota.fecha_cancelacion
                                  or datetime.now(timezone.utc))
    if payload.facturas:
        nota.total = sum((d.importe for d in payload.facturas), Decimal("0"))
    elif payload.total is not None:
        nota.total = payload.total
    elif nota.total is None:
        nota.total = Decimal("0")
    db.flush()

    if payload.facturas:
        facturas = _facturas_por_ref(db, ctx, payload.empresa, payload.facturas)
        db.query(NotaCreditoFactura).filter(
            NotaCreditoFactura.nota_id == nota.id
        ).delete(synchronize_session=False)
        for d in payload.facturas:
            f = facturas.get((_norm_serie(d.serie), d.folio))
            db.add(NotaCreditoFactura(
                tenant_id=ctx.tenant_id, nota_id=nota.id,
                factura_id=f.id if f else None, factura_ref=_ref(d), importe=d.importe,
            ))
        db.flush()
    return {"id": str(nota.id), "estado": nota.estado, "total": nota.total,
            "facturas": len(payload.facturas)}
