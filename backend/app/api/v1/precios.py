"""Precios v2: cotización (precio resuelto) y overrides por cliente/sucursal.

- GET /precios/cotizar — resuelve el precio para (cliente, sucursal, serie,
  proyecto, producto, presentación, cantidad); read gated por `menu:productos`
  (lo usan ventas/POS).
- Overrides CRUD — precios especiales negociados; read `menu:listas_precios`,
  write `lista_precios:gestionar`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, Producto, PrecioOverride, Sucursal
from ...schemas.common import Page
from ...schemas.sucursal import CotizacionOut, PrecioOverrideCreate, PrecioOverrideOut
from ...models import Tenant
from ...services.precios import resolver_asignaciones, resolver_precio
from ._helpers import ensure_fk, get_or_404, paginate

router = APIRouter(prefix="/precios", tags=["precios"])

_READ_COTIZAR = "menu:productos"
_READ_OVR = "menu:listas_precios"
_WRITE_OVR = "lista_precios:gestionar"


@router.post("/cotizar-documento")
def cotizar_documento_endpoint(
    archivo: UploadFile = File(...),
    cliente_id: UUID = Form(...),
    sucursal_id: Optional[UUID] = Form(default=None),
    serie_id: Optional[UUID] = Form(default=None),
    proyecto_id: Optional[UUID] = Form(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ_COTIZAR)),
):
    """El «WhatsApp cotizador» en la pantalla: PDF/foto/Excel de una orden →
    la cotización de todos los renglones con los precios de ESE cliente. Lo
    que no cruza con confianza sale en `sin_cruce` para revisarse a mano."""
    from ...core.ratelimit import enforce
    from ...services import cotizador

    enforce(f"cotizador-ia:{ctx.tenant_id}", 60, 3600)
    data = archivo.file.read(10 * 1024 * 1024 + 1)
    if not data or len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Archivo vacío o mayor a 10 MB")
    try:
        return cotizador.cotizar_documento(
            db, ctx.tenant_id, cliente_id=cliente_id, data=data,
            filename=archivo.filename or "documento",
            sucursal_id=sucursal_id, serie_id=serie_id, proyecto_id=proyecto_id,
        )
    except cotizador.CotizadorError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/cotizacion-pdf")
def cotizacion_pdf_endpoint(
    payload: dict = Body(...),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ_COTIZAR)),
):
    """La cotización recién calculada, como PDF para mandarla. El frontend
    regresa el MISMO objeto que devolvió /cotizar-documento."""
    from ...services import cotizador

    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    contenido = cotizador.cotizacion_pdf(
        str(payload.get("cliente_nombre") or ""), tenant.legal_name or "", payload)
    return Response(content=contenido, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="cotizacion.pdf"'})


@router.get("/listas-del-cliente")
def listas_del_cliente(
    cliente_id: UUID = Query(...),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ_COTIZAR)),
):
    """Las listas que le aplican a un cliente (globales, por sucursal y por
    proyecto), para ofrecer sus descargas en el cotizador."""
    from ...models import ListaPrecios, Proyecto

    filas = resolver_asignaciones(db, cliente_id=cliente_id)
    proys = {str(r.proyecto_id) for r in filas if r.proyecto_id}
    # también las de los proyectos del cliente, aunque el documento no traiga proyecto
    for pr in db.query(Proyecto).filter(Proyecto.cliente_id == cliente_id, Proyecto.deleted_at.is_(None)):
        for a in resolver_asignaciones(db, cliente_id=cliente_id, proyecto_id=pr.id):
            if a.proyecto_id:
                filas.append(a)
    vistos, out = set(), []
    nombres_proy = {p.id: p.nombre for p in db.query(Proyecto).filter(Proyecto.deleted_at.is_(None))}
    for a in filas:
        if a.lista_id in vistos:
            continue
        vistos.add(a.lista_id)
        lp = db.query(ListaPrecios).filter(ListaPrecios.id == a.lista_id, ListaPrecios.deleted_at.is_(None)).one_or_none()
        if lp is None:
            continue
        alcance = ("Proyecto " + (nombres_proy.get(a.proyecto_id) or "")) if a.proyecto_id else (
            "Sucursal" if a.sucursal_id else "General")
        out.append({"lista_id": str(lp.id), "nombre": lp.nombre, "alcance": alcance})
    return {"listas": out}


@router.get("/cotizar", response_model=CotizacionOut)
def cotizar(
    producto_id: UUID = Query(...),
    presentacion: str = Query(default="KILO", max_length=20),
    cantidad: Decimal = Query(default=Decimal("1"), gt=0),
    cliente_id: Optional[UUID] = Query(default=None),
    sucursal_id: Optional[UUID] = Query(default=None),
    # Las otras dos dimensiones de la negociación. Se piden aquí porque el
    # documento las conoce (la serie con la que se folia, el proyecto de la
    # orden) y sin ellas la cotización no puede ser la que se va a cobrar.
    serie_id: Optional[UUID] = Query(default=None),
    proyecto_id: Optional[UUID] = Query(default=None),
    fecha: Optional[date] = Query(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ_COTIZAR)),
):
    res = resolver_precio(
        db, producto_id=producto_id, presentacion=presentacion, cantidad=cantidad,
        cliente_id=cliente_id, sucursal_id=sucursal_id,
        serie_id=serie_id, proyecto_id=proyecto_id, fecha=fecha,
    )
    return CotizacionOut(
        producto_id=producto_id, presentacion=presentacion, cantidad=cantidad,
        precio=(res or {}).get("precio"),
        origen=(res or {}).get("origen"),
        lista_id=(res or {}).get("lista_id"),
    )


@router.get("/overrides", response_model=Page[PrecioOverrideOut])
def list_overrides(
    cliente_id: Optional[UUID] = Query(default=None),
    sucursal_id: Optional[UUID] = Query(default=None),
    producto_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ_OVR)),
):
    query = db.query(PrecioOverride)
    if cliente_id is not None:
        query = query.filter(PrecioOverride.cliente_id == cliente_id)
    if sucursal_id is not None:
        query = query.filter(PrecioOverride.sucursal_id == sucursal_id)
    if producto_id is not None:
        query = query.filter(PrecioOverride.producto_id == producto_id)
    query = query.order_by(PrecioOverride.created_at.desc())
    return paginate(query, PrecioOverrideOut, limit, offset)


@router.post("/overrides", response_model=PrecioOverrideOut, status_code=status.HTTP_201_CREATED)
def create_override(
    payload: PrecioOverrideCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE_OVR)),
):
    ensure_fk(db, Producto, payload.producto_id, "producto_id")
    ensure_fk(db, Cliente, payload.cliente_id, "cliente_id")
    ensure_fk(db, Sucursal, payload.sucursal_id, "sucursal_id")
    obj = PrecioOverride(**payload.model_dump(), tenant_id=ctx.tenant_id)
    db.add(obj)
    db.flush()
    db.refresh(obj)
    return obj


@router.delete("/overrides/{override_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_override(
    override_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE_OVR)),
):
    obj = get_or_404(db, PrecioOverride, override_id, soft=False)
    db.delete(obj)
    db.flush()
    return None
