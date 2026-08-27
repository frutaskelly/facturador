"""SAT code suggestion (AI-assisted, human-confirmed).

Gated by `producto:gestionar` (the people who create products). The endpoint
never mutates data — it returns a suggestion the user confirms in the UI.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ...core.ratelimit import enforce
from ...core.rbac import AuthContext, get_tenant_db, require_permission
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
