"""Lo que el Facturador puede preguntarle a SAE en vivo.

Existe para una sola cosa: que las preguntas que DECIDEN —«¿esta orden ya está
facturada AHORA?»— se contesten desde el Facturador y no desde la puerta propia
del bot. Lo que solo CONTESTA sigue saliendo del espejo, que se sincroniza cada
media hora y cuadra con SAE al peso.

No es una pasarela de SQL: cada ruta arma su consulta y lo de afuera viaja como
parámetro. Y estas rutas son de SOLO LECTURA: escribir en SAE (altas y cambios
de producto) también lo hace el Facturador desde el 26-sep-2026, pero por su
cola y su propio usuario (`services/sae_escritura.py`), nunca desde aquí.
"""
import threading
import time
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, require_duenio_de_sae, require_permission
from ...core.rbac import get_tenant_db
from ...schemas.sae import SaeCatalogosOut
from ...services import espejo_sae, sae_lectura


# El SAE que lee este router es UNO y es de un solo tenant: el candado cuelga
# del router entero para que una ruta nueva no nazca abierta.
router = APIRouter(prefix="/sae", tags=["sae"],
                   dependencies=[Depends(require_duenio_de_sae)])

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
    q: Optional[str] = Query(default=None, min_length=3, max_length=120,
                             description="Texto que busca en la OBSERVACIÓN del documento (la OC)"),
    doc: Optional[str] = Query(default=None, max_length=40,
                               description="CVE_DOC exacto («ZEHMOVH 1442»), sin importar los espacios"),
    tipo: str = Query(default="factura", pattern="^(factura|pedido)$",
                      description="«factura» (FACTF) o «pedido» (FACTP)"),
    limite: int = Query(default=50, ge=1, le=200),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Las facturas de SAE que mencionan ese texto en su observación, EN VIVO.

    Es la misma pregunta que el espejo contesta más barato; ésta es para cuando
    la respuesta decide algo y no puede ir media hora atrasada — sobre todo
    cuando la respuesta es «no hay ninguna», que es la que lleva a facturar dos
    veces si llega equivocada.
    """
    if not (q or doc):
        raise HTTPException(status_code=422, detail="hace falta `q` (la observación) o `doc`")
    if not sae_lectura.disponible():
        raise HTTPException(
            status_code=503,
            detail="el Facturador no tiene acceso a SAE; pregunta al espejo o revisa la configuración",
        )
    try:
        if doc:
            uno = sae_lectura.documento_por_clave(empresa, doc, tipo=tipo)
            filas = [uno] if uno else []
        else:
            filas = sae_lectura.documentos_de(empresa, q.strip(), tipo=tipo, limite=limite)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except sae_lectura.SAENoDisponible as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SAE no contestó: {type(e).__name__}: {e}")
    return {"ok": True, "empresa": empresa, "q": (q or "").strip() or None, "doc": doc,
            "tipo": tipo, "total": len(filas), "facturas": filas, "fuente": "SAE_EN_VIVO"}


@router.get("/partidas")
def partidas(
    empresa: str = Query(..., max_length=4),
    docs: str = Query(..., max_length=4000,
                      description="CVE_DOC separados por coma («ZEHMOVH 1442,ZEHMOVH 1443»)"),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Las partidas de esas facturas: clave, cantidad, precio e importe.

    Por lote a propósito: con ochenta documentos, ir de a uno tarda minutos.
    """
    if not sae_lectura.disponible():
        raise HTTPException(status_code=503,
                            detail="el Facturador no tiene acceso a SAE")
    lista = [d.strip() for d in docs.split(",") if d.strip()]
    try:
        filas = sae_lectura.partidas_de(empresa, lista)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except sae_lectura.SAENoDisponible as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SAE no contestó: {type(e).__name__}: {e}")
    return {"ok": True, "empresa": empresa, "documentos": len(lista),
            "total": len(filas), "partidas": filas, "fuente": "SAE_EN_VIVO"}


# Las series de SAE por empresa, las mismas con las que el bot espeja hoy.
_SERIES_POR_EMPRESA = {
    "02": ("ZHGO", "ZEHMOHOS", "ZMAFAN", "ZEHMOFAC", "ZECA"),
    "03": ("ZEHMOVH",),
    "04": ("ZEHMOTG", "ZSUR", "ZDIF", "ZBPT", "ZCH5C", "ZCS", "MIN5C", "ZVIDA"),
}


@router.post("/espejo/jalar")
def jalar_espejo(
    empresa: str = Query(..., max_length=4),
    series: Optional[str] = Query(default=None,
                                  description="Series separadas por coma; por omisión las de esa empresa"),
    dias: int = Query(default=3, ge=0, le=60,
                      description="Ventana para revisar cancelaciones de lo ya reflejado"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """El Facturador se trae de SAE lo que le falta, sin pasar por el bot.

    La marca de agua es el propio espejo: se pide a SAE solo lo posterior al
    folio más alto que ya se tiene de cada serie, así que la pasada es barata
    aunque corra seguido. Aparte se revisa una ventana corta por si algo se
    canceló, que es el cambio que la marca de agua no puede ver.
    """
    if not sae_lectura.disponible():
        raise HTTPException(status_code=503, detail="el Facturador no tiene acceso a SAE")
    lista = [s.strip() for s in (series or "").split(",") if s.strip()]
    if not lista:
        lista = list(_SERIES_POR_EMPRESA.get(empresa, ()))
    if not lista:
        raise HTTPException(status_code=422, detail=f"no sé qué series tiene la empresa {empresa}")
    try:
        return {"ok": True, **espejo_sae.sincronizar(db, ctx, empresa, lista, dias_cancelaciones=dias)}
    except sae_lectura.SAENoDisponible as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"la pasada del espejo falló: {type(e).__name__}: {e}")


@router.post("/espejo/cuadre")
def cuadrar_espejo(
    empresa: str = Query(..., max_length=4),
    series: Optional[str] = Query(default=None),
    reparar: bool = Query(default=True,
                          description="Trae las que falten, hasta el tope"),
    tope: int = Query(default=25, ge=0, le=200,
                      description="Más faltantes que esto no se reparan solas"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """¿El espejo tiene TODAS las facturas que SAE tiene? Cuenta contra cuenta.

    La marca de agua pide lo posterior al folio más alto, así que **no puede
    ver un hueco por debajo**: una factura que se haya perdido queda congelada
    para siempre. Esto la encuentra contando, y trae las que falten — hasta el
    tope, porque si faltan trescientas eso no es un hueco, es que algo se
    rompió, y repararlo a escondidas taparía el problema.
    """
    if not sae_lectura.disponible():
        raise HTTPException(status_code=503, detail="el Facturador no tiene acceso a SAE")
    lista = [s.strip() for s in (series or "").split(",") if s.strip()] or \
        list(_SERIES_POR_EMPRESA.get(empresa, ()))
    if not lista:
        raise HTTPException(status_code=422, detail=f"no sé qué series tiene la empresa {empresa}")
    try:
        return {"ok": True, **espejo_sae.cuadre(db, ctx, empresa, lista,
                                                reparar=reparar, tope=tope)}
    except sae_lectura.SAENoDisponible as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"el cuadre falló: {type(e).__name__}: {e}")


# ── Catálogos de SAE para dar de alta o cambiar un producto ──────────────────

# Líneas y esquemas cambian casi nunca, y el bot los pide cada vez que arma un
# alta: sin caché, cada «dale de alta…» por WhatsApp serían dos consultas a SAE
# por la red del Mini. Quince minutos en memoria, por empresa. Lo que falla NO
# se guarda: la siguiente pregunta vuelve a intentar.
_CATALOGOS_TTL_SEG = 15 * 60
_catalogos_cache: dict[str, tuple[float, dict]] = {}
_catalogos_lock = threading.Lock()


def _es_de_red(e: Exception) -> bool:
    """¿El error dice «SAE no está» más que «la consulta está mal»? Sin red, sin
    login o con timeout pymssql lanza OperationalError/InterfaceError; se
    reconocen por nombre porque el driver ni siquiera se importa sin SAE."""
    return (isinstance(e, (sae_lectura.SAENoDisponible, OSError, TimeoutError))
            or type(e).__name__ in ("OperationalError", "InterfaceError"))


@router.get("/catalogos", response_model=SaeCatalogosOut)
def catalogos(
    empresa: str = Query(default="02", pattern=r"^\d{2}$",
                         description="Empresa de SAE: 02, 03, 04, 05"),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Las líneas, los esquemas de impuestos y las unidades de SAE para esa
    empresa: con qué se puede dar de alta o cambiar un artículo (26-sep-2026).

    Líneas y esquemas salen EN VIVO de SAE (CLIN e IMPU), porque ahí se crean y
    el espejo no los trae; con quince minutos de caché. Las unidades no son de
    SAE sino del escritor: las que `sae_escritura` sabe traducir a UNI_MED y a
    la clave de unidad del SAT, una por unidad.

    Sin SAE contesta 503 con el motivo: el bot le dice al usuario que SAE no
    está, en vez de ofrecerle una lista vieja o vacía como si fuera la buena.
    """
    from ...services.sae_escritura import UNIDADES_CANONICAS

    ahora = time.monotonic()
    with _catalogos_lock:
        guardado = _catalogos_cache.get(empresa)
    if guardado and ahora - guardado[0] < _CATALOGOS_TTL_SEG:
        return guardado[1]

    if not sae_lectura.disponible():
        raise HTTPException(status_code=503,
                            detail="el Facturador no tiene acceso a SAE; no puedo leer líneas ni esquemas")
    try:
        lineas = sae_lectura.lineas_de(empresa)
        esquemas = sae_lectura.esquemas_de(empresa)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        if _es_de_red(e):
            raise HTTPException(
                status_code=503,
                detail=f"SAE no contestó (empresa {empresa}): {type(e).__name__}: {e}")
        # No es la red: la consulta está mal, y eso es un bug que tiene que verse
        raise HTTPException(status_code=502,
                            detail=f"la consulta a SAE falló: {type(e).__name__}: {e}")

    salida = {"empresa": empresa, "lineas": lineas, "esquemas": esquemas,
              "unidades": sorted(UNIDADES_CANONICAS)}
    with _catalogos_lock:
        _catalogos_cache[empresa] = (ahora, salida)
    return salida
