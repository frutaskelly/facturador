"""Listas de precios, sus precios y a QUIÉN se le aplican — CRUD.

Tres routers en un módulo porque son la misma pantalla:

  · `/listas-precios`        — la lista y sus renglones de precio.
  · `/asignaciones-precios`  — a qué cliente/sucursal/serie/proyecto aplica cada
    lista. Vive en su propio prefijo, y no bajo `/listas-precios/…`, para que
    "asignaciones" no compita con `/{lista_id}` al enrutar.

Reads gated por `menu:listas_precios`; writes por `lista_precios:gestionar`.

Las listas se borran en suave (las referencian asignaciones y documentos); los
precios y las asignaciones se borran de verdad: son configuración barata que se
vuelve a capturar.
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_auth_context, get_tenant_db, require_permission
from ...models import (
    Cliente,
    ListaAsignacion,
    ListaPrecios,
    Precio,
    Producto,
    Proyecto,
    Serie,
    Sucursal,
)
from ...schemas.lista_precios import (
    ListaAsignacionCreate,
    ListaAsignacionOut,
    ListaAsignacionUpdate,
    ListaAsignarIn,
    ListaAsignarOut,
    ListaPreciosCreate,
    ListaPreciosOut,
    ListaPreciosUpdate,
    PrecioBulkRequest,
    PrecioBulkResult,
    PrecioCopiarRequest,
    PrecioCreate,
    PrecioOut,
    PrecioUpdate,
)
from ...schemas.common import Page
from ...services.precios import resolver_asignacion
from ._helpers import ensure_fk, flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/listas-precios", tags=["listas de precios"])

_READ = "menu:listas_precios"
_WRITE = "lista_precios:gestionar"
_DUP_LISTA = "Ya existe una lista de precios con ese código"
_DUP_PRECIO = "Ya existe un precio para ese producto/presentación/cantidad en la lista"


# ─── price lists ─────────────────────────────────────────────────────────────
@router.get("", response_model=Page[ListaPreciosOut])
def list_listas(
    q: Optional[str] = Query(default=None, max_length=254),
    status_: Optional[str] = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(ListaPrecios).filter(ListaPrecios.deleted_at.is_(None))
    if q:
        like = f"%{q}%"
        query = query.filter(
            ListaPrecios.nombre.ilike(like) | ListaPrecios.codigo.ilike(like)
        )
    if status_:
        query = query.filter(ListaPrecios.status == status_)
    query = query.order_by(ListaPrecios.codigo.asc())
    return paginate(query, ListaPreciosOut, limit, offset)


@router.post("", response_model=ListaPreciosOut, status_code=status.HTTP_201_CREATED)
def create_lista(
    payload: ListaPreciosCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = ListaPrecios(**payload.model_dump(), tenant_id=ctx.tenant_id)
    db.add(obj)
    flush_or_conflict(db, detail=_DUP_LISTA)
    db.refresh(obj)
    return obj


@router.get("/{lista_id}", response_model=ListaPreciosOut)
def get_lista(
    lista_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, ListaPrecios, lista_id)


@router.patch("/{lista_id}", response_model=ListaPreciosOut)
def update_lista(
    lista_id: UUID,
    payload: ListaPreciosUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, ListaPrecios, lista_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)
    flush_or_conflict(db, detail=_DUP_LISTA)
    db.refresh(obj)
    return obj


@router.delete("/{lista_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lista(
    lista_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("lista_precios:eliminar")),
):
    obj = get_or_404(db, ListaPrecios, lista_id)
    obj.deleted_at = func.now()
    db.flush()
    return None


# ─── copy prices from another list ───────────────────────────────────────────
@router.post("/{lista_id}/copiar", response_model=PrecioBulkResult)
def copiar_precios(
    lista_id: UUID,
    payload: PrecioCopiarRequest,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Copy ALL precios from `origen_id` into `lista_id`, skipping duplicates.

    A row is a duplicate when the destination already has a precio for the same
    (producto, presentación, cantidad_minima) tier.
    """
    get_or_404(db, ListaPrecios, lista_id)
    get_or_404(db, ListaPrecios, payload.origen_id)  # 404 if origen isn't ours
    if payload.origen_id == lista_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La lista de origen no puede ser la misma que el destino",
        )

    origen_precios = db.query(Precio).filter(Precio.lista_id == payload.origen_id).all()
    existing = {
        (p.producto_id, p.presentacion, p.cantidad_minima)
        for p in db.query(Precio).filter(Precio.lista_id == lista_id).all()
    }

    created = 0
    skipped = 0
    for src in origen_precios:
        key = (src.producto_id, src.presentacion, src.cantidad_minima)
        if key in existing:
            skipped += 1
            continue
        db.add(
            Precio(
                tenant_id=ctx.tenant_id,
                lista_id=lista_id,
                producto_id=src.producto_id,
                presentacion=src.presentacion,
                precio_unitario=src.precio_unitario,
                cantidad_minima=src.cantidad_minima,
                vigencia_desde=src.vigencia_desde,
                vigencia_hasta=src.vigencia_hasta,
            )
        )
        existing.add(key)
        created += 1

    db.flush()
    return PrecioBulkResult(created=created, updated=0, skipped=skipped)


# ─── prices (nested under a list) ────────────────────────────────────────────
@router.get("/{lista_id}/precios", response_model=Page[PrecioOut])
def list_precios(
    lista_id: UUID,
    producto_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    get_or_404(db, ListaPrecios, lista_id)  # 404 if the list isn't ours
    query = db.query(Precio).filter(Precio.lista_id == lista_id)
    if producto_id is not None:
        query = query.filter(Precio.producto_id == producto_id)
    query = query.order_by(Precio.producto_id.asc(), Precio.cantidad_minima.asc())
    return paginate(query, PrecioOut, limit, offset)


@router.post(
    "/{lista_id}/precios",
    response_model=PrecioOut,
    status_code=status.HTTP_201_CREATED,
)
def create_precio(
    lista_id: UUID,
    payload: PrecioCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    get_or_404(db, ListaPrecios, lista_id)
    ensure_fk(db, Producto, payload.producto_id, "producto_id")
    obj = Precio(
        **payload.model_dump(),
        tenant_id=ctx.tenant_id,
        lista_id=lista_id,
    )
    db.add(obj)
    flush_or_conflict(db, detail=_DUP_PRECIO)
    db.refresh(obj)
    return obj


@router.post("/{lista_id}/precios/bulk", response_model=PrecioBulkResult)
def bulk_upsert_precios(
    lista_id: UUID,
    payload: PrecioBulkRequest,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Upsert many precios at once.

    For each item, if a precio already exists for the same
    (producto, presentación, cantidad_minima) tier its `precio_unitario` is
    overwritten; otherwise a new row is created. Productos outside the tenant
    scope are rejected.
    """
    get_or_404(db, ListaPrecios, lista_id)

    existing = {
        (p.producto_id, p.presentacion, p.cantidad_minima): p
        for p in db.query(Precio).filter(Precio.lista_id == lista_id).all()
    }
    validated_productos: set = set()

    created = 0
    updated = 0
    for item in payload.items:
        if item.producto_id not in validated_productos:
            ensure_fk(db, Producto, item.producto_id, "producto_id")
            validated_productos.add(item.producto_id)
        key = (item.producto_id, item.presentacion, item.cantidad_minima)
        current = existing.get(key)
        if current is not None:
            current.precio_unitario = item.precio_unitario
            updated += 1
        else:
            obj = Precio(
                tenant_id=ctx.tenant_id,
                lista_id=lista_id,
                producto_id=item.producto_id,
                presentacion=item.presentacion,
                precio_unitario=item.precio_unitario,
                cantidad_minima=item.cantidad_minima,
            )
            db.add(obj)
            existing[key] = obj
            created += 1

    flush_or_conflict(db, detail=_DUP_PRECIO)
    return PrecioBulkResult(created=created, updated=updated, skipped=0)


@router.patch("/{lista_id}/precios/{precio_id}", response_model=PrecioOut)
def update_precio(
    lista_id: UUID,
    precio_id: UUID,
    payload: PrecioUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = (
        db.query(Precio)
        .filter(Precio.id == precio_id, Precio.lista_id == lista_id)
        .one_or_none()
    )
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Precio no encontrado")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)
    flush_or_conflict(db, detail=_DUP_PRECIO)
    db.refresh(obj)
    return obj


@router.delete(
    "/{lista_id}/precios/{precio_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_precio(
    lista_id: UUID,
    precio_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = (
        db.query(Precio)
        .filter(Precio.id == precio_id, Precio.lista_id == lista_id)
        .one_or_none()
    )
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Precio no encontrado")
    db.delete(obj)
    db.flush()
    return None


@router.post("/{lista_id}/asignar", response_model=ListaAsignarOut)
def asignar_lista(
    lista_id: UUID,
    payload: ListaAsignarIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Asignar la lista como DEFAULT del negocio (la usan los clientes sin lista
    propia) y/o a clientes específicos — el último paso del wizard de
    importación, también disponible desde la administración de listas."""
    lista = get_or_404(db, ListaPrecios, lista_id)
    if payload.default:
        # Solo puede haber una default: se limpia la anterior.
        db.query(ListaPrecios).filter(
            ListaPrecios.es_default.is_(True), ListaPrecios.id != lista.id
        ).update({"es_default": False})
        lista.es_default = True
    asignados = 0
    for cid in payload.cliente_ids:
        cli = (
            db.query(Cliente)
            .filter(Cliente.id == cid, Cliente.deleted_at.is_(None))
            .one_or_none()
        )
        if cli is None:
            continue
        # Asignación GLOBAL del cliente: sin sucursal, serie ni proyecto, o sea
        # "estos precios en todo el país". Si ya tenía una, se reapunta en vez
        # de duplicarla — el wizard se corre varias veces y no debe multiplicar.
        actual = (
            db.query(ListaAsignacion)
            .filter(
                ListaAsignacion.cliente_id == cid,
                ListaAsignacion.sucursal_id.is_(None),
                ListaAsignacion.serie_id.is_(None),
                ListaAsignacion.proyecto_id.is_(None),
                ListaAsignacion.vigencia_desde.is_(None),
            )
            .one_or_none()
        )
        if actual is None:
            db.add(ListaAsignacion(tenant_id=ctx.tenant_id, lista_id=lista.id, cliente_id=cid))
        else:
            actual.lista_id = lista.id
        asignados += 1
    db.flush()
    return ListaAsignarOut(default=lista.es_default, clientes_asignados=asignados)


# ─── Excel de ida y vuelta + PDF (28-ago-2026, pedido del dueño) ────────────

def _ctx_descarga(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """Descargas de la lista: además del lector normal (menu:listas_precios),
    el usuario del PORTAL (menu:cotizador con candado por cliente) puede bajar
    las listas QUE LE APLICAN — la verificación por lista va en _lista_descargable."""
    if ctx.has(_READ):
        return ctx
    if ctx.cliente_scope and ctx.has("menu:cotizador"):
        return ctx
    raise HTTPException(status_code=403, detail="Sin permiso para descargar listas de precios")


def _lista_descargable_o_403(db: Session, ctx: AuthContext, lista_id: UUID) -> None:
    """Para el usuario con candado: la lista debe estar asignada a alguno de
    SUS clientes. Un lector normal baja cualquiera (como siempre)."""
    if not ctx.cliente_scope or ctx.has(_READ):
        return
    from ...services.precios import listas_asignadas_a_cliente

    for cid in ctx.cliente_scope:
        if lista_id in listas_asignadas_a_cliente(db, cid):
            return
    raise HTTPException(status_code=403, detail="Esa lista no es de tus clientes")


@router.get("/{lista_id}/export")
def exportar_lista_xlsx(
    lista_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(_ctx_descarga),
):
    """El Excel de la lista (SKU | PRODUCTO | PRESENTACION | DESDE CANTIDAD |
    PRECIO): se edita y se vuelve a subir con /importar para actualizar en masa."""
    from ...services import lista_export

    _lista_descargable_o_403(db, ctx, lista_id)
    lista = get_or_404(db, ListaPrecios, lista_id)
    contenido = lista_export.exportar_xlsx(db, lista)
    nombre = f"precios_{(lista.codigo or 'lista').strip()}.xlsx"
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.get("/{lista_id}/pdf")
def exportar_lista_pdf(
    lista_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(_ctx_descarga),
):
    from ...models import Tenant
    from ...services import lista_export

    _lista_descargable_o_403(db, ctx, lista_id)
    lista = get_or_404(db, ListaPrecios, lista_id)
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    contenido = lista_export.exportar_pdf(db, lista, tenant.legal_name or "")
    nombre = f"lista_{(lista.codigo or 'precios').strip()}.pdf"
    return Response(content=contenido, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{nombre}"'})


@router.post("/{lista_id}/importar")
def importar_lista_xlsx(
    lista_id: UUID,
    archivo: UploadFile = File(...),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Sube el MISMO Excel del export para actualizar en masa: PRECIO con valor
    crea/actualiza el renglón; PRECIO vacío o 0 lo QUITA de la lista. Los SKU
    desconocidos se reportan (los productos nuevos nacen en el wizard)."""
    from ...services import lista_export

    lista = get_or_404(db, ListaPrecios, lista_id)
    data = archivo.file.read(10 * 1024 * 1024 + 1)
    if not data or len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Archivo vacío o mayor a 10 MB")
    res = lista_export.importar_xlsx(db, ctx.tenant_id, lista, data)
    if not res.get("ok"):
        raise HTTPException(status_code=422, detail=res.get("error"))
    return res


# ─── a QUIÉN se le aplica cada lista ─────────────────────────────────────────
router_asignaciones = APIRouter(prefix="/asignaciones-precios", tags=["listas de precios"])

_DUP_ASIGNACION = (
    "Ya existe una asignación para esa combinación (mismo cliente, sucursal, "
    "serie, proyecto y fecha de inicio)"
)


def _validar_coherencia(db: Session, cliente_id, sucursal_id, proyecto_id) -> None:
    """Impide asignaciones que no pueden aplicar nunca.

    Una sucursal de otro cliente, o un proyecto de otro cliente, forman un
    renglón que jamás coincide con ningún documento: se guarda, se ve en la
    tabla y no cobra nada. Es peor que un error, porque parece configurado.
    """
    if cliente_id and sucursal_id:
        suc = db.query(Sucursal.cliente_id).filter(Sucursal.id == sucursal_id).first()
        if suc and suc[0] != cliente_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Esa sucursal no es del cliente elegido",
            )
    if cliente_id and proyecto_id:
        pro = db.query(Proyecto.cliente_id).filter(Proyecto.id == proyecto_id).first()
        if pro and pro[0] is not None and pro[0] != cliente_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Ese proyecto no es del cliente elegido",
            )


def _con_nombres(db: Session, filas: list[ListaAsignacion]) -> list[ListaAsignacionOut]:
    """Resuelve los nombres de las cuatro dimensiones en 5 consultas, no en 5×N.

    La tabla de asignaciones se lee de un vistazo o no sirve: "EHMO · Pachuca ·
    ZEHMOHOS · HOSPITALES" dice algo; cuatro UUID no dicen nada.
    """
    def _mapa(model, campo, ids):
        ids = {i for i in ids if i}
        if not ids:
            return {}
        return {
            row[0]: row[1]
            for row in db.query(model.id, campo).filter(model.id.in_(ids)).all()
        }

    listas = _mapa(ListaPrecios, ListaPrecios.nombre, [f.lista_id for f in filas])
    clientes = _mapa(Cliente, Cliente.legal_name, [f.cliente_id for f in filas])
    sucursales = _mapa(Sucursal, Sucursal.nombre, [f.sucursal_id for f in filas])
    series = _mapa(Serie, Serie.codigo, [f.serie_id for f in filas])
    proyectos = _mapa(Proyecto, Proyecto.nombre, [f.proyecto_id for f in filas])

    salida = []
    for f in filas:
        out = ListaAsignacionOut.model_validate(f)
        out.lista_nombre = listas.get(f.lista_id)
        out.cliente_nombre = clientes.get(f.cliente_id)
        out.sucursal_nombre = sucursales.get(f.sucursal_id)
        out.serie_codigo = series.get(f.serie_id)
        out.proyecto_nombre = proyectos.get(f.proyecto_id)
        salida.append(out)
    return salida


@router_asignaciones.get("", response_model=Page[ListaAsignacionOut])
def list_asignaciones(
    lista_id: Optional[UUID] = Query(default=None),
    cliente_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(ListaAsignacion)
    if lista_id is not None:
        query = query.filter(ListaAsignacion.lista_id == lista_id)
    if cliente_id is not None:
        query = query.filter(ListaAsignacion.cliente_id == cliente_id)
    # De la más específica a la más general: es el orden en que se aplican.
    query = query.order_by(
        ListaAsignacion.especificidad.desc(), ListaAsignacion.created_at.desc()
    )
    total = query.order_by(None).count()
    filas = query.offset(offset).limit(limit).all()
    return Page[ListaAsignacionOut](
        items=_con_nombres(db, filas), total=total, limit=limit, offset=offset
    )


@router_asignaciones.get("/simular", response_model=Optional[ListaAsignacionOut])
def simular_asignacion(
    cliente_id: Optional[UUID] = Query(default=None),
    sucursal_id: Optional[UUID] = Query(default=None),
    serie_id: Optional[UUID] = Query(default=None),
    proyecto_id: Optional[UUID] = Query(default=None),
    fecha: Optional[date] = Query(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Qué asignación ganaría para esa combinación — o null si ninguna.

    Es la misma función que usa el resolutor de precios, expuesta para que se
    pueda comprobar ANTES de emitir un documento en vez de descubrirlo en el PDF.
    """
    ganadora = resolver_asignacion(
        db, cliente_id=cliente_id, sucursal_id=sucursal_id,
        serie_id=serie_id, proyecto_id=proyecto_id, fecha=fecha,
    )
    return _con_nombres(db, [ganadora])[0] if ganadora is not None else None


@router_asignaciones.post("", response_model=ListaAsignacionOut, status_code=status.HTTP_201_CREATED)
def create_asignacion(
    payload: ListaAsignacionCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ensure_fk(db, ListaPrecios, payload.lista_id, "lista_id")
    ensure_fk(db, Cliente, payload.cliente_id, "cliente_id")
    ensure_fk(db, Sucursal, payload.sucursal_id, "sucursal_id")
    ensure_fk(db, Serie, payload.serie_id, "serie_id")
    ensure_fk(db, Proyecto, payload.proyecto_id, "proyecto_id")
    _validar_coherencia(db, payload.cliente_id, payload.sucursal_id, payload.proyecto_id)
    obj = ListaAsignacion(**payload.model_dump(), tenant_id=ctx.tenant_id)
    db.add(obj)
    flush_or_conflict(db, detail=_DUP_ASIGNACION)
    db.refresh(obj)
    return _con_nombres(db, [obj])[0]


@router_asignaciones.patch("/{asignacion_id}", response_model=ListaAsignacionOut)
def update_asignacion(
    asignacion_id: UUID,
    payload: ListaAsignacionUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Cambia la lista o la vigencia. Las DIMENSIONES no se editan: cambiarlas
    es otra negociación, y reusar el renglón borraría el rastro de la anterior."""
    obj = get_or_404(db, ListaAsignacion, asignacion_id, soft=False)
    data = payload.model_dump(exclude_unset=True)
    if data.get("lista_id") is not None:
        ensure_fk(db, ListaPrecios, data["lista_id"], "lista_id")
    for key, value in data.items():
        setattr(obj, key, value)
    flush_or_conflict(db, detail=_DUP_ASIGNACION)
    db.refresh(obj)
    return _con_nombres(db, [obj])[0]


@router_asignaciones.delete("/{asignacion_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asignacion(
    asignacion_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, ListaAsignacion, asignacion_id, soft=False)
    db.delete(obj)
    db.flush()
    return None
