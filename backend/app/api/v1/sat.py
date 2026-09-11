"""SAT code suggestion (AI-assisted, human-confirmed).

Gated by `producto:gestionar` (the people who create products). The endpoint
never mutates data — it returns a suggestion the user confirms in the UI.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ...core.ratelimit import enforce
from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models.sat_catalogo import SatClaveProdServ, SatClaveUnidad
from ...schemas.sat import SatSugerenciaIn, SatSugerenciaOut
from ...services.sat_ai import SatAIUnavailable, sugerir_sat
from ...services.sat_catalogo import buscar_claves, buscar_unidades, validar_clave

router = APIRouter(prefix="/sat", tags=["sat"])


@router.post("/sugerir", response_model=SatSugerenciaOut)
def sugerir(
    payload: SatSugerenciaIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("producto:gestionar")),
):
    # Cada llamada cuesta dinero de API (Claude) — tope por tenant.
    enforce(f"sat-ia:{ctx.tenant_id}", 120, 3600)
    try:
        sugerencia = sugerir_sat(payload.nombre, payload.descripcion)
    except SatAIUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    # Solo claves que EXISTEN en el catálogo SAT oficial cargado en la base; si
    # la IA no dio ninguna válida, caen los mejores candidatos por texto.
    validas = [o for o in sugerencia["opciones"] if validar_clave(db, o["clave_sat"])]
    if not validas:
        validas = [
            {"clave_sat": c["clave"], "descripcion": c["descripcion"]}
            for c in buscar_claves(db, payload.nombre, limit=4)
        ]
    if validas:
        sugerencia["opciones"] = validas
    return sugerencia


@router.get("/claves")
def buscar_claves_sat(
    q: str = Query(min_length=2, max_length=254),
    limit: int = Query(default=10, ge=1, le=30),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("menu:productos")),
):
    """Búsqueda en el catálogo SAT oficial (c_ClaveProdServ) cargado en la base:
    por texto (FTS español + variantes) o por prefijo de clave. Alimenta el
    autocompletar del formulario de producto y del wizard de importación."""
    return buscar_claves(db, q, limit=limit)


@router.get("/unidades")
def buscar_unidades_sat(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=10, ge=1, le=30),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("menu:productos")),
):
    """Búsqueda en el catálogo SAT de unidades (c_ClaveUnidad)."""
    return buscar_unidades(db, q, limit=limit)


@router.get("/describir")
def describir_sat(
    claves: str = Query(default="", max_length=4000),
    unidades: str = Query(default="", max_length=1000),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("menu:productos")),
):
    """Descripción oficial de claves ProdServ y unidades CONCRETAS (separadas
    por coma), en un solo viaje. Es lo que pone «50401700 — Chiles frescos» al
    lado del puro número en la vinculación/importación de productos (ticket
    86bbyvyaj): una clave que no aparece en la respuesta no existe en el
    catálogo oficial."""
    pedidas_c = list({c.strip() for c in claves.split(",") if c.strip()})[:300]
    pedidas_u = list({u.strip().upper() for u in unidades.split(",") if u.strip()})[:100]
    out_claves: dict[str, str] = {}
    if pedidas_c:
        rows = db.query(SatClaveProdServ).filter(SatClaveProdServ.clave.in_(pedidas_c)).all()
        out_claves = {r.clave: r.descripcion for r in rows}
    out_unidades: dict[str, str] = {}
    if pedidas_u:
        rows = db.query(SatClaveUnidad).filter(SatClaveUnidad.clave.in_(pedidas_u)).all()
        out_unidades = {r.clave: r.nombre for r in rows}
    return {"claves": out_claves, "unidades": out_unidades}
