"""SAT code suggestion (AI-assisted, human-confirmed).

Gated by `producto:gestionar` (the people who create products). The endpoint
never mutates data — it returns a suggestion the user confirms in the UI.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from ...core.ratelimit import enforce
from ...core.rbac import AuthContext, require_permission
from ...schemas.sat import SatSugerenciaIn, SatSugerenciaOut
from ...services.sat_ai import SatAIUnavailable, sugerir_sat

router = APIRouter(prefix="/sat", tags=["sat"])


@router.post("/sugerir", response_model=SatSugerenciaOut)
def sugerir(
    payload: SatSugerenciaIn,
    ctx: AuthContext = Depends(require_permission("producto:gestionar")),
):
    # Cada llamada cuesta dinero de API (Claude) — tope por tenant.
    enforce(f"sat-ia:{ctx.tenant_id}", 120, 3600)
    try:
        return sugerir_sat(payload.nombre, payload.descripcion)
    except SatAIUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
