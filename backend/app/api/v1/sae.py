"""Lo que el Facturador puede preguntarle a SAE en vivo.

Existe para una sola cosa: que las preguntas que DECIDEN —«¿esta orden ya está
facturada AHORA?»— se contesten desde el Facturador y no desde la puerta propia
del bot. Lo que solo CONTESTA sigue saliendo del espejo, que se sincroniza cada
media hora y cuadra con SAE al peso.

No es una pasarela de SQL: cada ruta arma su consulta y lo de afuera viaja como
parámetro. Y es de SOLO LECTURA — las escrituras a SAE siguen siendo del bot y
de su cola.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.rbac import AuthContext, require_permission
from ...services import sae_lectura

router = APIRouter(prefix="/sae", tags=["sae"])

_LEER = "factura:espejo"   # el mismo permiso con el que el bot ya lee el espejo


@router.get("/salud")
def salud(ctx: AuthContext = Depends(require_permission(_LEER))):
    """¿El Facturador puede hablar con SAE? Sin tocar la red si no hay config."""
    if not sae_lectura.disponible():
        return {"ok": True, "disponible": False,
                "motivo": "el Facturador no tiene configurado el acceso a SAE"}
    try:
        sae_lectura.consultar("SELECT 1 AS uno")
        return {"ok": True, "disponible": True}
    except sae_lectura.SAENoDisponible as e:
        return {"ok": True, "disponible": False, "motivo": str(e)}
    except Exception as e:
        return {"ok": True, "disponible": False, "motivo": f"{type(e).__name__}: {e}"}


@router.get("/facturas")
def facturas(
    empresa: str = Query(..., max_length=4, description="Empresa de SAE: 02, 03, 04…"),
    q: str = Query(..., min_length=3, max_length=120,
                   description="Texto que busca en la OBSERVACIÓN del documento (la OC)"),
    limite: int = Query(default=50, ge=1, le=200),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Las facturas de SAE que mencionan ese texto en su observación, EN VIVO.

    Es la misma pregunta que el espejo contesta más barato; ésta es para cuando
    la respuesta decide algo y no puede ir media hora atrasada — sobre todo
    cuando la respuesta es «no hay ninguna», que es la que lleva a facturar dos
    veces si llega equivocada.
    """
    if not sae_lectura.disponible():
        raise HTTPException(
            status_code=503,
            detail="el Facturador no tiene acceso a SAE; pregunta al espejo o revisa la configuración",
        )
    try:
        filas = sae_lectura.facturas_de(empresa, q.strip(), limite=limite)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except sae_lectura.SAENoDisponible as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SAE no contestó: {type(e).__name__}: {e}")
    return {"ok": True, "empresa": empresa, "q": q.strip(),
            "total": len(filas), "facturas": filas, "fuente": "SAE_EN_VIVO"}
