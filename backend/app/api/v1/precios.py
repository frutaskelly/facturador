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

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, Producto, PrecioOverride, Sucursal
from ...schemas.common import Page
from ...schemas.sucursal import CotizacionOut, PrecioOverrideCreate, PrecioOverrideOut
from ...services.precios import resolver_precio
from ._helpers import ensure_fk, get_or_404, paginate

router = APIRouter(prefix="/precios", tags=["precios"])

_READ_COTIZAR = "menu:productos"
_READ_OVR = "menu:listas_precios"
_WRITE_OVR = "lista_precios:gestionar"


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
