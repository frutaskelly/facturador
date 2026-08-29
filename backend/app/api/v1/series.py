"""Series de folios — CRUD.

Reads gated por `menu:series`; writes por `serie:gestionar`. Una serie con folios
ya emitidos (folio_actual > 0) no se elimina (rompería la consecutividad).
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Factura, Remision, Serie
from ...schemas.common import Page
from ...schemas.serie import (
    SerieCreate,
    SerieFolioSugerido,
    SerieOut,
    SeriePairCreate,
    SerieUpdate,
)
from ...services.series import resolver_serie
from ._helpers import flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/series", tags=["series"])

_READ = "menu:series"
_WRITE = "serie:gestionar"
_DUP = "Ya existe una serie con ese código para ese tipo de documento"


def _max_folio_emitido(db: Session, serie: Serie) -> int:
    """Folio más alto realmente emitido con el código de esta serie. Es el piso
    al editar `folio_actual`: por debajo se generarían folios duplicados
    (decisión 2026-07-29 #7)."""
    if serie.tipo_documento == "FACTURA":
        mx = (
            db.query(func.max(Factura.folio))
            .filter(Factura.serie == serie.codigo)
            .scalar()
        )
        return int(mx or 0)
    # Remisiones guardan "<codigo><folio>" en folio_interno.
    mx = 0
    rows = (
        db.query(Remision.folio_interno)
        .filter(Remision.folio_interno.ilike(f"{serie.codigo}%"))
        .all()
    )
    for (f,) in rows:
        num = (f or "")[len(serie.codigo):].lstrip("-")
        if num.isdigit():
            mx = max(mx, int(num))
    return mx


def _clear_default(db: Session, tenant_id, tipo_documento: str, *, except_id=None) -> None:
    """Quita es_default de las demás series del mismo tipo (una sola default por tipo)."""
    q = db.query(Serie).filter(
        Serie.tenant_id == tenant_id,
        Serie.tipo_documento == tipo_documento,
        Serie.es_default.is_(True),
    )
    if except_id is not None:
        q = q.filter(Serie.id != except_id)
    for s in q.all():
        s.es_default = False
    db.flush()


@router.get("", response_model=Page[SerieOut])
def list_series(
    tipo_documento: Optional[str] = Query(default=None, max_length=20),
    tipo: Optional[str] = Query(default=None, max_length=10),
    activa: Optional[bool] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(Serie)
    if tipo_documento:
        query = query.filter(Serie.tipo_documento == tipo_documento)
    if tipo:
        query = query.filter(Serie.tipo == tipo)
    if activa is not None:
        query = query.filter(Serie.activa.is_(activa))
    query = query.order_by(Serie.tipo_documento, Serie.codigo)
    return paginate(query, SerieOut, limit, offset)


@router.get("/resolver", response_model=Optional[SerieOut])
def resolver(
    tipo_documento: str = Query(max_length=20),
    cliente_id: Optional[UUID] = Query(default=None),
    sucursal_id: Optional[UUID] = Query(default=None),
    serie_id: Optional[UUID] = Query(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Serie que aplicaría al emitir (override → sucursal → cliente → default)."""
    return resolver_serie(
        db, ctx.tenant_id, tipo_documento,
        serie_id=serie_id, sucursal_id=sucursal_id, cliente_id=cliente_id,
    )


@router.get("/{serie_id}", response_model=SerieOut)
def get_serie(
    serie_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, Serie, serie_id, soft=False)


@router.get("/{serie_id}/folio-sugerido", response_model=SerieFolioSugerido)
def folio_sugerido(
    serie_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """En qué folio dejar la serie al cortar un cliente de SAE.

    Convención del contador: `consumir_folio` incrementa ANTES de asignar
    (`UPDATE … folio_actual + 1 RETURNING`), así que `folio_actual` guarda el
    ÚLTIMO folio emitido, no el siguiente. Para que el primer CFDI propio salga
    justo después del último que emitió SAE, `folio_actual` debe quedar EN ese
    último: por eso `sugerido` es el máximo de las espejo tal cual, SIN sumarle
    uno. Un +1 aquí saltaría un folio y el SAT vería un hueco en la serie.

    El máximo sale de las facturas espejo (origen='ESPEJO_SAE'), que son los
    CFDI que SAE ya timbró con esta serie. Solo aplica a series de FACTURA: en
    una de remisión o de pago el código podría coincidir con el de una serie
    fiscal y la sugerencia movería un contador que nada tiene que ver.
    """
    serie = get_or_404(db, Serie, serie_id, soft=False)
    espejo_max = None
    espejo_n = 0
    if serie.tipo_documento == "FACTURA":
        # El espejo guarda la serie de SAE normalizada a mayúsculas (POST
        # /facturas/espejo), así que el código de la serie se compara igual.
        espejo_n, espejo_max = (
            db.query(func.count(Factura.id), func.max(Factura.folio))
            .filter(
                Factura.origen == "ESPEJO_SAE",
                Factura.serie == (serie.codigo or "").strip().upper(),
                Factura.deleted_at.is_(None),
            )
            .one()
        )
    return SerieFolioSugerido(
        serie=serie.codigo,
        folio_actual=serie.folio_actual or 0,
        folio_espejo_max=int(espejo_max) if espejo_max is not None else None,
        sugerido=int(espejo_max) if espejo_max is not None else None,
        facturas_espejo=espejo_n,
    )


@router.post("", response_model=SerieOut, status_code=status.HTTP_201_CREATED)
def create_serie(
    payload: SerieCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    if payload.es_default:
        _clear_default(db, ctx.tenant_id, payload.tipo_documento)
    obj = Serie(**payload.model_dump(), tenant_id=ctx.tenant_id)
    db.add(obj)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.post("/par", response_model=List[SerieOut], status_code=status.HTTP_201_CREATED)
def create_serie_par(
    payload: SeriePairCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Crea la serie de factura (FISCAL) y la de remisión (NO_FISCAL) en una operación."""
    if payload.es_default:
        _clear_default(db, ctx.tenant_id, "FACTURA")
        _clear_default(db, ctx.tenant_id, "REMISION")
    factura = Serie(
        tenant_id=ctx.tenant_id, codigo=payload.codigo_factura, tipo="FISCAL",
        tipo_documento="FACTURA", nombre=payload.nombre, folio_actual=payload.folio_inicial_factura,
        es_default=payload.es_default, activa=True,
    )
    remision = Serie(
        tenant_id=ctx.tenant_id, codigo=payload.codigo_remision, tipo="NO_FISCAL",
        tipo_documento="REMISION", nombre=payload.nombre, folio_actual=payload.folio_inicial_remision,
        es_default=payload.es_default, activa=True,
    )
    db.add_all([factura, remision])
    flush_or_conflict(db, detail=_DUP)
    db.refresh(factura)
    db.refresh(remision)
    return [factura, remision]


@router.patch("/{serie_id}", response_model=SerieOut)
def update_serie(
    serie_id: UUID,
    payload: SerieUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Serie, serie_id, soft=False)
    data = payload.model_dump(exclude_unset=True)
    if data.get("es_default"):
        _clear_default(db, ctx.tenant_id, obj.tipo_documento, except_id=obj.id)
    if "folio_actual" in data and data["folio_actual"] is not None:
        piso = _max_folio_emitido(db, obj)
        if data["folio_actual"] < piso:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"El folio no puede quedar por debajo del último emitido "
                    f"({obj.codigo}{piso}); el mínimo permitido es {piso}"
                ),
            )
    for key, value in data.items():
        setattr(obj, key, value)
    db.flush()
    db.refresh(obj)
    return obj


@router.delete("/{serie_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_serie(
    serie_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("serie:eliminar")),
):
    obj = get_or_404(db, Serie, serie_id, soft=False)
    if obj.folio_actual and obj.folio_actual > 0:
        raise HTTPException(status_code=409, detail="La serie ya emitió folios; desactívala en vez de eliminarla")
    db.delete(obj)
    db.flush()
    return None
