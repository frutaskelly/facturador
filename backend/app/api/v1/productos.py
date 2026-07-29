"""Productos — CRUD.

Reads gated by `menu:productos` (so a TOMADOR can look products up while taking
an order); writes by `producto:gestionar`. The optional `categoria_id` and
`esquema_impuesto_id` FKs are re-validated under the tenant scope before they
are persisted (RLS does not constrain Postgres FK checks).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.ratelimit import enforce
from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import CategoriaProducto, EsquemaImpuesto, Producto, ProductoAlias
from ...schemas.producto import (
    AliasIn,
    CandidatoOut,
    LineaPegadaOut,
    MatchIn,
    MatchResultOut,
    ParsePegadoIn,
    ProductoCreate,
    ProductoOut,
    ProductoUpdate,
)
from ...schemas.common import Page
from ...services.producto_match import (
    aprender_alias,
    buscar,
    normalizar,
    parsear_pegado,
    productos_activos,
    sugerir_con_ia,
)
from ._helpers import ensure_fk, flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/productos", tags=["productos"])

_READ = "menu:productos"
_WRITE = "producto:gestionar"
_DUP = "Ya existe un producto con ese SKU"


def _validate_fks(db: Session, *, categoria_id, esquema_impuesto_id) -> None:
    ensure_fk(db, CategoriaProducto, categoria_id, "categoria_id")
    ensure_fk(db, EsquemaImpuesto, esquema_impuesto_id, "esquema_impuesto_id")


def _next_sku(db: Session) -> str:
    """Next 8-digit sequential SKU for the tenant. Only fully-numeric existing
    SKUs participate in the sequence (legacy alphanumeric SKUs are ignored)."""
    mx = 0
    rows = (
        db.query(Producto.sku)
        .filter(Producto.sku.op("~")("^[0-9]+$"))
        .all()
    )
    for (sku,) in rows:
        try:
            mx = max(mx, int(sku))
        except (TypeError, ValueError):
            pass
    return f"{mx + 1:08d}"


def _similar_filter(query, term: str):
    """Match a term against nombre, sku, descripción y sinónimos (ilike)."""
    like = f"%{term}%"
    return query.filter(
        Producto.nombre.ilike(like)
        | Producto.sku.ilike(like)
        | Producto.descripcion.ilike(like)
        | func.array_to_string(Producto.sinonimos, " ").ilike(like)
    )


@router.get("", response_model=Page[ProductoOut])
def list_productos(
    q: Optional[str] = Query(default=None, max_length=254),
    categoria_id: Optional[UUID] = Query(default=None),
    activo: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(Producto).filter(Producto.deleted_at.is_(None))
    if q:
        query = _similar_filter(query, q.strip())
    if categoria_id is not None:
        query = query.filter(Producto.categoria_id == categoria_id)
    if activo is not None:
        query = query.filter(Producto.activo.is_(activo))
    query = query.order_by(Producto.nombre.asc())
    return paginate(query, ProductoOut, limit, offset)


@router.get("/similares", response_model=list[ProductoOut])
def productos_similares(
    nombre: str = Query(min_length=2, max_length=254),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Posibles duplicados: productos cuyo nombre/sinónimos coinciden. Se llama
    antes de crear para evitar dar de alta dos veces el mismo bien (jitomate vs
    tomate). Declarado antes de /{producto_id} para no capturarse como UUID."""
    query = _similar_filter(
        db.query(Producto).filter(Producto.deleted_at.is_(None)), nombre.strip()
    )
    rows = query.order_by(Producto.nombre.asc()).limit(10).all()
    return [ProductoOut.model_validate(r) for r in rows]


@router.post("/match", response_model=list[MatchResultOut])
def match_productos(
    payload: MatchIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Cruza textos libres (tecleados/pegados) contra el catálogo: exacto → alias
    aprendido → difuso, y opcionalmente IA para los que no resuelvan."""
    if payload.usar_ia:
        # La rama IA manda el catálogo completo como contexto (cuesta dinero).
        enforce(f"producto-ia:{ctx.tenant_id}", 120, 3600)
    catalogo = productos_activos(db)   # una sola carga para todos los textos
    resultados: list[dict] = []
    sin_match: list[str] = []
    for texto in payload.textos:
        cands = buscar(db, ctx.tenant_id, texto, limit=payload.limit, prods=catalogo)
        resultados.append({"texto": texto, "candidatos": [
            CandidatoOut(
                producto_id=c.producto_id, sku=c.sku, nombre=c.nombre, score=c.score, origen=c.origen,
                presentaciones=c.presentaciones, presentacion_default=c.presentacion_default,
                unidad_base=c.unidad_base,
            )
            for c in cands
        ]})
        if not cands:
            sin_match.append(texto)

    if payload.usar_ia and sin_match:
        ia = sugerir_con_ia(db, ctx.tenant_id, sin_match)
        pids = {pid for pid in ia.values() if pid}
        prods = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(pids)).all()} if pids else {}
        for r in resultados:
            pid = ia.get(r["texto"])
            if not r["candidatos"] and pid and pid in prods:
                p = prods[pid]
                r["candidatos"] = [CandidatoOut(
                    producto_id=p.id, sku=p.sku, nombre=p.nombre, score=85, origen="ia",
                    presentaciones=p.presentaciones or {}, presentacion_default=p.presentacion_default,
                    unidad_base=p.unidad_base,
                )]
    return resultados


@router.post("/parse-pegado", response_model=list[LineaPegadaOut])
def parse_pegado(
    payload: ParsePegadoIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Convierte un bloque pegado desde Excel en líneas estructuradas (detecta
    columnas en cualquier orden y salta el encabezado, con IA) y cruza cada
    producto contra el catálogo (exacto → alias → difuso → IA). Declarado antes
    de /{producto_id} para no capturarse como UUID."""
    if payload.usar_ia:
        enforce(f"producto-ia:{ctx.tenant_id}", 120, 3600)
    filas = parsear_pegado(payload.texto, usar_ia=payload.usar_ia)
    if not filas:
        return []

    catalogo = productos_activos(db)   # una sola carga para todas las filas
    resultados: list[dict] = []
    sin_match: list[str] = []
    for f in filas:
        # Varios candidatos para poblar el desplegable Match IA (el front muestra ≥80%).
        cands = buscar(db, ctx.tenant_id, f["producto"], limit=8, prods=catalogo)
        resultados.append({
            "texto": f["producto"],
            "cantidad": f["cantidad"],
            "precio": f["precio"],
            "presentacion": f["presentacion"],
            "candidatos": [
                CandidatoOut(
                    producto_id=c.producto_id, sku=c.sku, nombre=c.nombre, score=c.score, origen=c.origen,
                    presentaciones=c.presentaciones, presentacion_default=c.presentacion_default,
                    unidad_base=c.unidad_base,
                )
                for c in cands
            ],
        })
        if not cands:
            sin_match.append(f["producto"])

    # IA solo para los que ni exacto/alias/difuso resolvieron (sinónimos regionales).
    if payload.usar_ia and sin_match:
        ia = sugerir_con_ia(db, ctx.tenant_id, sin_match)
        pids = {pid for pid in ia.values() if pid}
        prods = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(pids)).all()} if pids else {}
        for r in resultados:
            pid = ia.get(r["texto"])
            if not r["candidatos"] and pid and pid in prods:
                p = prods[pid]
                r["candidatos"] = [CandidatoOut(
                    producto_id=p.id, sku=p.sku, nombre=p.nombre, score=85, origen="ia",
                    presentaciones=p.presentaciones or {}, presentacion_default=p.presentacion_default,
                    unidad_base=p.unidad_base,
                )]
    return resultados


@router.post("/alias", status_code=status.HTTP_201_CREATED)
def crear_alias(
    payload: AliasIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Aprende un alias confirmado por el usuario: el próximo cruce lo resuelve solo.

    Decisión 2026-07-29 (#3): ENSEÑAR un alias nuevo lo puede hacer cualquiera
    con lectura (es el flujo natural de captura), pero RE-APUNTAR uno ya
    aprendido a otro producto exige `producto:gestionar` — cambiar lo aprendido
    envenenaría el cruce de todo el negocio."""
    ensure_fk(db, Producto, payload.producto_id, "producto_id")
    existente = (
        db.query(ProductoAlias)
        .filter(ProductoAlias.alias_normalizado == normalizar(payload.texto))
        .one_or_none()
    )
    if (
        existente is not None
        and existente.producto_id != payload.producto_id
        and _WRITE not in ctx.permissions
        and not ctx.is_owner
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "El alias ya apunta a otro producto; re-apuntarlo requiere el "
                "permiso de gestión de productos"
            ),
        )
    aprender_alias(db, ctx.tenant_id, payload.texto, payload.producto_id, origen="MANUAL", user_id=ctx.user_id)
    return {"ok": True}


@router.post("", response_model=ProductoOut, status_code=status.HTTP_201_CREATED)
def create_producto(
    payload: ProductoCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    _validate_fks(
        db,
        categoria_id=payload.categoria_id,
        esquema_impuesto_id=payload.esquema_impuesto_id,
    )
    data = payload.model_dump()
    if data.get("nombre"):
        data["nombre"] = data["nombre"].strip().upper()   # nombres siempre en mayúsculas
    if not (data.get("sku") or "").strip():
        data["sku"] = _next_sku(db)   # auto-generate when blank
    obj = Producto(**data, tenant_id=ctx.tenant_id)
    db.add(obj)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.get("/{producto_id}", response_model=ProductoOut)
def get_producto(
    producto_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, Producto, producto_id)


@router.patch("/{producto_id}", response_model=ProductoOut)
def update_producto(
    producto_id: UUID,
    payload: ProductoUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Producto, producto_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("nombre"):
        data["nombre"] = data["nombre"].strip().upper()   # nombres siempre en mayúsculas
    if "categoria_id" in data:
        ensure_fk(db, CategoriaProducto, data["categoria_id"], "categoria_id")
    if "esquema_impuesto_id" in data:
        ensure_fk(db, EsquemaImpuesto, data["esquema_impuesto_id"], "esquema_impuesto_id")
    for key, value in data.items():
        setattr(obj, key, value)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.delete("/{producto_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_producto(
    producto_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Producto, producto_id)
    obj.deleted_at = func.now()
    db.flush()
    return None
