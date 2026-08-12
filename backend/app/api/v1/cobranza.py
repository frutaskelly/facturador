"""Cobranza — estado de cuenta del cliente (F1) y complementos de pago/REP (F2).

Estado de cuenta = Cuentas por Cobrar del cliente estilo SAE: sus facturas PPD
timbradas no saldadas (cargos), con antigüedad de saldos por FECHA DE
VENCIMIENTO (fecha de la factura + días de crédito del cliente) en intervalos de
30 días con una columna "Por vencer". Los abonos (REP) llegan en F2.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, Factura
from ._helpers import get_or_404

router = APIRouter(prefix="/cobranza", tags=["cobranza"])

_READ = "menu:facturas"   # quien ve facturas ve la cobranza (F1); permiso propio en F2


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
