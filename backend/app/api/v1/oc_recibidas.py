"""Bandeja de órdenes de compra — ingesta desatendida y revisión humana.

Toda orden entra por aquí (WhatsApp, correo, captura) antes de volverse
remisión. El backend intenta resolver el cliente y la sucursal con las
equivalencias registradas; lo que no resuelve NO se adivina ni se descarta:
queda PENDIENTE con su motivo para que alguien lo cierre desde la UI.

Por qué la bandeja y no crear la remisión directo: la remisión no puede guardar
de dónde vino el documento, ni qué decía el original, ni que el sistema dudó. Y
crear una remisión de un cliente adivinado quema un folio de la serie que ya no
se recupera.

Permisos: se reusan los de remisiones (`menu:remisiones` / `remision:gestionar`)
— la bandeja ES la antesala de la remisión, y así el rol del bot no necesita
permisos nuevos ni una migración de catálogo.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Almacen, Cliente, OCRecibida, Producto, Remision, Sucursal
from ...schemas.common import Page
from ...schemas.oc_recibida import (
    CrearRemisionIn,
    OCRecibidaDetailOut,
    OCRecibidaIn,
    OCRecibidaOut,
    OCRecibidaUpdate,
)
from ...services import cliente_match
from ...services.producto_match import (
    aprender_alias,
    buscar,
    alias_del_tenant,
    normalizar_catalogo,
    productos_activos,
)
from ._helpers import ensure_fk, get_or_404, paginate

router = APIRouter(prefix="/oc-recibidas", tags=["bandeja de OC"])

_READ = "menu:remisiones"
_WRITE = "remision:gestionar"

# Sistema de equivalencia ← campo del payload de ingesta.
# `ubicacion` NO está aquí: un punto de entrega dice DÓNDE se descarga, no a
# quién se le factura (Balles y Jubran comparten los mismos puntos). Se usa solo
# para el destino, ya con el cliente decidido, y su texto viaja siempre a las
# observaciones del documento.
_PISTAS = (
    ("RFC", "rfc"),
    ("SAE", "clave_sae"),
    ("PROYECTO", "proyecto"),
    ("NOMBRE", "nombre"),
    ("WHATSAPP", "jid"),   # no decide: aporta la lista corta de candidatos
)


def _pistas_de(payload: dict) -> list[cliente_match.Pista]:
    """Arma las pistas del documento. PROYECTO y UBICACION se namespacean con el
    perfil del bot: 'HOSPITALES' significa cosas distintas en Pachuca y en
    Villahermosa, y sin el prefijo una equivalencia pisaría a la otra."""
    perfil = (payload.get("perfil") or "").strip().lower()
    pistas: list[cliente_match.Pista] = []
    for sistema, campo in _PISTAS:
        valor = (payload.get(campo) or "").strip()
        if not valor:
            continue
        if sistema == "PROYECTO":
            # Sin perfil el prefijo no existe y la clave caería en un espacio
            # global: 'HOSPITALES' significa cosas distintas en Pachuca y en
            # Villahermosa, y una pisaría a la otra. Sin perfil, no hay pista.
            if not perfil:
                continue
            valor = f"{perfil}:{valor}"
        pistas.append(cliente_match.Pista(sistema=sistema, clave=valor))
    return pistas


def _resolver_y_aplicar(db: Session, oc: OCRecibida) -> None:
    """(Re)resuelve cliente y destino de una OC desde su payload."""
    payload = oc.payload or {}
    res = cliente_match.resolver(db, oc.tenant_id, _pistas_de(payload))

    oc.ambiguo = res.ambiguo
    oc.resuelto_via = res.via
    oc.cliente_id = res.cliente_id
    oc.sucursal_id = None
    # La lista corta que se le ofrece al operador cuando el grupo no alcanza a
    # decidir (por el de Pachuca entran EHMO y MAFAN; por el de Hidalgo, Balles
    # y Jubran). Se guarda para que la bandeja no tenga que recalcularla.
    oc.candidatos = [str(c) for c in res.candidatos] or None

    # El punto de entrega (hospital, plantel) es texto del documento y va SIEMPRE
    # a las observaciones, resuelva o no una sucursal. Es lo que el equipo lee
    # para saber a dónde llevar la mercancía.
    oc.punto_entrega = (payload.get("ubicacion") or "").strip() or None

    if res.cliente_id is not None and oc.punto_entrega:
        oc.sucursal_id = cliente_match.resolver_destino(
            db, oc.tenant_id, res.cliente_id, oc.punto_entrega,
            perfil=str(payload.get("perfil") or ""),
        )

    if res.cliente_id is None:
        oc.estado = "PENDIENTE"
        oc.motivo = res.motivo
    elif not payload.get("lineas"):
        oc.estado = "PENDIENTE"
        oc.motivo = "El documento no trae partidas legibles"
    else:
        # Cliente resuelto: falta que un humano (o el paso de crear-remisión)
        # cruce los productos. Se queda PENDIENTE hasta que exista la remisión —
        # ASIGNADA significa "ya nació su remisión", no "ya sé de quién es".
        oc.estado = "PENDIENTE"
        oc.motivo = (
            f"Falta decir a qué sucursal pertenece «{oc.punto_entrega}»"
            if oc.sucursal_id is None and oc.punto_entrega
            else "Lista para revisar y crear la remisión"
        )


@router.post("", response_model=OCRecibidaDetailOut, status_code=status.HTTP_201_CREATED)
def ingesta(
    payload: OCRecibidaIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Recibe una OC ya parseada. IDEMPOTENTE por `origen_externo`.

    Un reintento (timeout de red del bot a media madrugada) actualiza el payload
    de la orden que ya existe y devuelve 200 en vez de crear una segunda. Si esa
    orden ya generó su remisión, no se toca nada: el documento ya está capturado.
    """
    data = payload.model_dump(mode="json")
    existente = (
        db.query(OCRecibida)
        .filter(OCRecibida.origen_externo == payload.origen_externo)
        .one_or_none()
    )
    if existente is not None:
        if existente.remision_id is not None or existente.estado == "DESCARTADA":
            return _detalle(db, existente)
        existente.payload = data
        existente.folio_externo = payload.folio_externo
        existente.remitente = payload.remitente
        existente.archivo_nombre = payload.archivo_nombre
        existente.archivo_url = payload.archivo_url
        existente.updated_by = ctx.user_id
        # Solo se re-resuelve lo que nadie ha tocado. Un reintento por timeout no
        # puede borrar la asignación que un humano ya hizo en la bandeja; para
        # forzar el recálculo está /reabrir, que es explícito.
        if existente.resuelto_via != "MANUAL":
            _resolver_y_aplicar(db, existente)
        db.flush()
        db.refresh(existente)
        return _detalle(db, existente)

    oc = OCRecibida(
        tenant_id=ctx.tenant_id,
        canal=payload.canal,
        origen_externo=payload.origen_externo,
        folio_externo=payload.folio_externo,
        remitente=payload.remitente,
        archivo_nombre=payload.archivo_nombre,
        archivo_url=payload.archivo_url,
        payload=data,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    _resolver_y_aplicar(db, oc)
    try:
        # Savepoint: si otro request insertó el mismo origen_externo en paralelo,
        # el UNIQUE truena aquí sin tirar la transacción y se devuelve el que ganó
        # (idempotencia real, no un 500 a media madrugada).
        with db.begin_nested():
            db.add(oc)
            db.flush()
    except IntegrityError:
        ganador = (
            db.query(OCRecibida)
            .filter(OCRecibida.origen_externo == payload.origen_externo)
            .one_or_none()
        )
        if ganador is None:
            raise HTTPException(status_code=409, detail="Orden duplicada")
        return _detalle(db, ganador)
    db.refresh(oc)
    return _detalle(db, oc)


@router.get("", response_model=Page[OCRecibidaOut])
def listar(
    estado: Optional[str] = Query(default=None, max_length=16),
    canal: Optional[str] = Query(default=None, max_length=20),
    cliente_id: Optional[UUID] = Query(default=None),
    sin_cliente: bool = Query(default=False),
    q: Optional[str] = Query(default=None, max_length=254),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(OCRecibida)
    if estado:
        query = query.filter(OCRecibida.estado == estado.upper())
    if canal:
        query = query.filter(OCRecibida.canal == canal.upper())
    if cliente_id:
        query = query.filter(OCRecibida.cliente_id == cliente_id)
    if sin_cliente:
        query = query.filter(OCRecibida.cliente_id.is_(None))
    if q:
        like = f"%{q}%"
        query = query.filter(
            OCRecibida.folio_externo.ilike(like)
            | OCRecibida.remitente.ilike(like)
            | OCRecibida.archivo_nombre.ilike(like)
        )
    return paginate(query.order_by(OCRecibida.recibida_at.desc()), OCRecibidaOut, limit, offset)


def _detalle(db: Session, oc: OCRecibida) -> dict:
    """Detalle + cruce de productos sugerido para cada partida.

    El cruce se calcula al vuelo (no se persiste): el catálogo cambia, y una
    sugerencia guardada hace meses miente. `productos_activos` se carga una vez
    para las N partidas en vez de una vez por partida.
    """
    payload = oc.payload or {}
    lineas_raw = payload.get("lineas") or []
    catalogo = productos_activos(db) if lineas_raw else []
    # Precalculado una vez para las N partidas: si no, el cruce es
    # O(partidas × productos) normalizaciones por cada apertura de la orden.
    norms = normalizar_catalogo(catalogo) if catalogo else {}
    aliases = alias_del_tenant(db) if catalogo else {}
    lineas = []
    for i, ln in enumerate(lineas_raw, start=1):
        texto = str(ln.get("descripcion") or "")
        cands = (
            buscar(db, oc.tenant_id, texto, limit=5, prods=catalogo, aliases=aliases, norms=norms)
            if texto else []
        )
        # La CLAVE del cliente suele ser más precisa que la descripción; si
        # resuelve, va primero.
        clave = str(ln.get("clave") or "").strip()
        if clave:
            por_clave = buscar(db, oc.tenant_id, clave, limit=3, prods=catalogo,
                               aliases=aliases, norms=norms)
            vistos = {c.producto_id for c in por_clave}
            cands = por_clave + [c for c in cands if c.producto_id not in vistos]
        lineas.append({
            "numero": i,
            "descripcion": texto,
            "cantidad": ln.get("cantidad") or 0,
            "unidad": ln.get("unidad"),
            "clave": ln.get("clave"),
            "precio": ln.get("precio"),
            "notas": ln.get("notas"),
            "candidatos": [
                {"producto_id": c.producto_id, "sku": c.sku, "nombre": c.nombre,
                 "score": c.score, "origen": c.origen,
                 "presentaciones": c.presentaciones or {},
                 "presentacion_default": c.presentacion_default}
                for c in cands[:5]
            ],
        })
    out = OCRecibidaDetailOut.model_validate(oc).model_dump()
    out["lineas"] = lineas
    return out


@router.get("/{oc_id}", response_model=OCRecibidaDetailOut)
def detalle(
    oc_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return _detalle(db, get_or_404(db, OCRecibida, oc_id, soft=False))


@router.patch("/{oc_id}", response_model=OCRecibidaDetailOut)
def asignar(
    oc_id: UUID,
    payload: OCRecibidaUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Corrige el cliente/sucursal de una OC desde la bandeja.

    Con `aprender=true` (default) la corrección se guarda como equivalencia
    CONFIRMADA para todas las pistas del documento: es el momento en que el
    sistema aprende, y por eso la próxima orden igual ya no pregunta.
    """
    oc = get_or_404(db, OCRecibida, oc_id, soft=False, for_update=True)
    if oc.remision_id is not None:
        raise HTTPException(
            status_code=409,
            detail="Esta orden ya generó su remisión; edítala desde la remisión",
        )
    if oc.estado == "DESCARTADA":
        # Aprender de un documento descartado envenena el cruce: si se descartó
        # es justamente porque no era de ese cliente. Se reabre primero.
        raise HTTPException(
            status_code=409, detail="Esta orden está descartada; reábrela antes de asignarla"
        )
    data = payload.model_dump(exclude_unset=True)

    if "cliente_id" in data and data["cliente_id"] is not None:
        ensure_fk(db, Cliente, data["cliente_id"], "cliente_id")
        oc.cliente_id = data["cliente_id"]
        oc.ambiguo = False
        oc.resuelto_via = "MANUAL"
    if "sucursal_id" in data:
        if data["sucursal_id"] is not None:
            suc = get_or_404(db, Sucursal, data["sucursal_id"])
            if suc.cliente_id != oc.cliente_id:
                raise HTTPException(
                    status_code=422, detail="La sucursal no pertenece al cliente de la orden"
                )
        oc.sucursal_id = data["sucursal_id"]
    if "folio_externo" in data:
        oc.folio_externo = data["folio_externo"]
    if "punto_entrega" in data:
        oc.punto_entrega = (data["punto_entrega"] or "").strip() or None
    if "motivo" in data:
        oc.motivo = data["motivo"]
    oc.updated_by = ctx.user_id

    if payload.aprender and oc.cliente_id is not None:
        # El punto de entrega se aprende como DESTINO: la próxima orden que diga
        # «JUAN GRAHAM» ya sabe que se descarga en la sucursal de Tabasco. No
        # vota por el cliente — Balles y Jubran comparten sus puntos de entrega.
        if oc.punto_entrega and oc.sucursal_id is not None:
            perfil = str((oc.payload or {}).get("perfil") or "").strip().lower()
            cliente_match.aprender(
                db, ctx.tenant_id, "UBICACION",
                f"{perfil}:{oc.punto_entrega}" if perfil else oc.punto_entrega,
                oc.cliente_id, sucursal_id=oc.sucursal_id,
                origen="MANUAL", confianza="CONFIRMADA", user_id=ctx.user_id,
            )
        for pista in _pistas_de(oc.payload or {}):
            # El JID es la pista MÁS DÉBIL: un mismo grupo recibe órdenes de
            # varias razones sociales (EHMO/MAFAN, Balles/Jubran). Aprenderlo
            # como confirmado por una sola corrección lo volvería decisorio y
            # asignaría en silencio las órdenes del otro cliente. Queda SUGERIDA:
            # se ve en la bandeja y se confirma a mano si el grupo es de uno solo.
            cliente_match.aprender(
                db, ctx.tenant_id, pista.sistema, pista.clave, oc.cliente_id,
                origen="MANUAL", confianza="CONFIRMADA", user_id=ctx.user_id,
            )
        # El grupo se registra como CANDIDATO de este cliente: no decide, pero la
        # próxima vez la bandeja ya ofrece la lista corta en vez del padrón entero.
        jid = str((oc.payload or {}).get("jid") or "").strip()
        if jid:
            cliente_match.aprender(
                db, ctx.tenant_id, "WHATSAPP", jid, oc.cliente_id,
                origen="MANUAL", confianza="CONFIRMADA", user_id=ctx.user_id,
            )

    db.flush()
    db.refresh(oc)
    return _detalle(db, oc)


@router.post("/{oc_id}/crear-remision", response_model=OCRecibidaDetailOut)
def crear_remision(
    oc_id: UUID,
    payload: CrearRemisionIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Convierte la OC en una remisión BORRADOR y las deja ligadas.

    La remisión se crea con el endpoint normal de remisiones (misma resolución
    de serie, precios e impuestos): esto solo prepara el cuerpo y estampa
    `origen_externo` para que la orden no pueda generar dos remisiones.
    """
    # Importado aquí y no arriba: remisiones.py importa este módulo indirectamente
    # a través de la app, y a nivel de módulo sería un ciclo.
    from .remisiones import create_remision
    from ...schemas.remision import LineaRemisionCreate, RemisionCreate

    oc = get_or_404(db, OCRecibida, oc_id, soft=False, for_update=True)
    if oc.remision_id is not None:
        raise HTTPException(status_code=409, detail="Esta orden ya generó su remisión")
    if oc.estado == "DESCARTADA":
        raise HTTPException(status_code=409, detail="Esta orden está descartada")
    if oc.cliente_id is None:
        raise HTTPException(
            status_code=422, detail="Asigna primero el cliente de la orden"
        )
    ensure_fk(db, Almacen, payload.almacen_id, "almacen_id")
    for ln in payload.lineas:
        ensure_fk(db, Producto, ln.producto_id, "producto_id")

    p = oc.payload or {}
    origen = f"OC:{oc.origen_externo}"[:120]
    folio = (oc.folio_externo or "").strip()
    # Las observaciones de la remisión: se imprimen en su PDF y pasan tal cual a
    # las de la factura al facturarla. El punto de entrega va primero porque es
    # lo que el equipo busca ahí. «OC <folio>» se conserva con ese formato exacto
    # porque es el ancla con la que ya se concilia contra SAE.
    notas = " · ".join(x for x in [
        oc.punto_entrega,
        f"OC {folio}" if folio else None,
        (p.get("observaciones") or "").strip() or None,
    ] if x)

    body = RemisionCreate(
        cliente_facturacion_id=oc.cliente_id,
        sucursal_id=oc.sucursal_id,
        almacen_id=payload.almacen_id,
        fecha_remision=payload.fecha_remision,
        fecha_entrega=payload.fecha_entrega or _fecha(p.get("fecha_entrega")),
        canal="API",
        notas=notas or None,
        nota_entrega=oc.punto_entrega,
        lineas=[
            LineaRemisionCreate(
                producto_id=ln.producto_id,
                presentacion=ln.presentacion,
                cantidad_solicitada=ln.cantidad,
                precio_unitario=ln.precio_unitario,
                notas=ln.notas,
            )
            for ln in payload.lineas
        ],
    )
    rem = create_remision(body, db=db, ctx=ctx)
    rem.origen_externo = origen

    # El cruce que acaba de confirmar el humano se aprende: la próxima orden que
    # diga "JITOMATE SALADET" ya sabe a qué producto va.
    # `producto_alias` es catálogo global del tenant: reapuntarlo afecta a TODO
    # el que cruce productos, no solo a esta remisión. Por eso pide el permiso
    # del catálogo y no basta con el de remisiones.
    if ctx.has("producto:gestionar"):
        for ln in payload.lineas:
            if ln.texto_original:
                aprender_alias(
                    db, ctx.tenant_id, ln.texto_original, ln.producto_id,
                    origen="IMPORT", user_id=ctx.user_id,
                )

    oc.remision_id = rem.id
    oc.estado = "ASIGNADA"
    oc.motivo = None
    oc.updated_by = ctx.user_id
    db.flush()
    db.refresh(oc)
    return _detalle(db, oc)


def _fecha(v) -> Optional[date]:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


@router.post("/{oc_id}/descartar", response_model=OCRecibidaOut)
def descartar(
    oc_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
    motivo: Optional[str] = Query(default=None, max_length=500),
):
    oc = get_or_404(db, OCRecibida, oc_id, soft=False, for_update=True)
    if oc.remision_id is not None:
        raise HTTPException(
            status_code=409,
            detail="Esta orden ya generó su remisión; cancela la remisión en su lugar",
        )
    oc.estado = "DESCARTADA"
    oc.motivo = motivo or "Descartada manualmente"
    oc.updated_by = ctx.user_id
    db.flush()
    db.refresh(oc)
    return oc


@router.post("/{oc_id}/reabrir", response_model=OCRecibidaDetailOut)
def reabrir(
    oc_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Regresa una descartada a PENDIENTE y vuelve a intentar el cruce."""
    oc = get_or_404(db, OCRecibida, oc_id, soft=False, for_update=True)
    if oc.remision_id is not None:
        raise HTTPException(status_code=409, detail="Esta orden ya generó su remisión")
    # Reabrir SÍ recalcula aunque la asignación fuera manual: es lo que se pide
    # explícitamente al pulsar el botón.
    oc.resuelto_via = None
    _resolver_y_aplicar(db, oc)
    oc.updated_by = ctx.user_id
    db.flush()
    db.refresh(oc)
    return _detalle(db, oc)
