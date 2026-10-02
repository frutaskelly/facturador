"""Productos — CRUD.

Reads gated by `menu:productos` (so a TOMADOR can look products up while taking
an order); writes by `producto:gestionar`. The optional `categoria_id` and
`esquema_impuesto_id` FKs are re-validated under the tenant scope before they
are persisted (RLS does not constrain Postgres FK checks).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import nullcontext
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ...core.ratelimit import enforce
from rapidfuzz import fuzz

from ...core.rbac import (AuthContext, es_duenio_de_sae, get_auth_context, get_tenant_db,
                          require_duenio_de_sae, require_permission)
from ...models import (
    CategoriaProducto,
    ClaveSae,
    Cliente,
    EsquemaImpuesto,
    ImportProductosLog,
    ListaAsignacion,
    ListaPrecios,
    Precio,
    Producto,
    ProductoAlias,
    ProductoCliente,
    SatClaveProdServ,
    SatClaveUnidad,
    SolicitudAltaSae,
    Sucursal,
)
from ...models.clave_sae import norm_clave
from ...schemas.producto import (
    ClaveSaeLineaIn,
    ClaveSaeLineaOut,
    AliasIn,
    AliasOut,
    AliasReapuntarIn,
    AltaSaeIn,
    AltaSaeOut,
    AltaSaeProductoIn,
    CambioSaeIn,
    AltaSaeReporteIn,
    CandidatoOut,
    CatalogoClienteBatchIn,
    CatalogoClienteBatchOut,
    ImportCategoriaMatch,
    ImportColumnaOut,
    ImportErrorFila,
    ImportFilaPreview,
    ImportIn,
    ImportPreviewOut,
    ImportProductoResultado,
    ImportResultOut,
    ImpuestoDeClaveOut,
    ImpuestosPorClaveIn,
    LineaPegadaOut,
    MatchIn,
    MatchResultOut,
    ParsePegadoIn,
    PresentacionCreate,
    ProductoCreate,
    ProductoOut,
    ProductoUpdate,
    SugerenciaCategoriaOut,
    SugerenciaEsquemaOut,
    SugerenciaSatOut,
    SugerirCategoriaBatchIn,
    SugerirEsquemaBatchIn,
    SugerirSatBatchIn,
    VocabularioOut,
)
from ...schemas.clave_sae import (
    ArticuloSaeEmpresaOut,
    ArticuloSaeOut,
    ClaveSaeBuscadaOut,
    ClaveSaeEstadoEmpresa,
    ClaveClienteOut,
    ClaveSaeEstadoOut,
    SolicitudSaeResumenOut,
)
from ...schemas.common import Page
from ...services.categoria_codigo import slugify_codigo
from ...services.importar_productos import (
    CAMPOS_MAPEABLES,
    ImportProductosError,
    analizar_columnas,
    extraer_con_ia,
    generar_plantilla,
    normalizar_unidad,
    parsear_plantilla,
)
from ...services.sat_catalogo import sugerir_batch
from ...services.catalogos_default import categoria_sin_categorizar
from ...services.sugerir_esquema import match_categorias, sugerir_categorias, sugerir_esquemas
from ...services.producto_match import (
    Candidato,
    alias_de_cliente,
    alias_del_tenant,
    aprender_alias,
    buscar,
    normalizar,
    normalizar_catalogo,
    normalizar_unidad_oc,
    parsear_pegado,
    productos_activos,
    sugerir_con_ia,
)
from ...services.inventario import claves_sae_por_presentacion, presentacion_declarada
from ...services.lista_export import clave_sae_de
from ...services.sucursales import es_sucursal_de
from ._helpers import ensure_fk, flush_or_conflict, get_or_404, paginate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/productos", tags=["productos"])

_READ = "menu:productos"
_WRITE = "producto:gestionar"
def _clave_sae_limpia(db: Session, ctx: AuthContext, data: dict, obj=None) -> None:
    """Normaliza la clave base de SAE igual que al comparar contra el espejo
    (SAE guarda CVE_ART con relleno y el cruce falla por un espacio).

    NO se defiende unicidad: una clave de SAE puede ampararse varios productos
    (regla del dueño, 23-sep-2026; la 0085 quitó el índice único)."""
    if isinstance(data.get("presentaciones"), dict):
        # La clave de cada presentación, con el mismo trato; vacía se quita
        # para que la línea vuelva a caer en la clave base.
        limpias = {}
        for nombre, raw in data["presentaciones"].items():
            if isinstance(raw, dict) and "clave_sae" in raw:
                raw = dict(raw)
                clave = str(raw.get("clave_sae") or "").strip().upper()[:50]
                if clave:
                    raw["clave_sae"] = clave
                else:
                    raw.pop("clave_sae")
            limpias[nombre] = raw
        data["presentaciones"] = limpias
    if "clave_sae" not in data:
        return
    clave = (data.get("clave_sae") or "").strip().upper()
    data["clave_sae"] = clave or None


def _unidades_sin_clave_sae(unidad_base, clave_sae, presentaciones) -> list[str]:
    """Las unidades del producto que se quedarían sin artículo de SAE: la base
    sin `clave_sae` y cada presentación distinta de la base sin la suya.

    La base sale con la clave del producto y las demás con la de su
    presentación (`clave_sae_de`): una presentación sin clave propia NO cae a
    la de la base, porque KILO y PIEZA son dos artículos distintos en SAE.
    """
    base = str(unidad_base or "").strip().upper()
    faltan = [] if str(clave_sae or "").strip() else [base or "la unidad base"]
    con_clave = claves_sae_por_presentacion(presentaciones)
    for nombre in (presentaciones or {}):
        n = str(nombre).strip().upper()
        if n and n != base and n not in con_clave and n not in faltan:
            faltan.append(n)
    return faltan


def _exigir_claves_sae(ctx: AuthContext, unidad_base, clave_sae, presentaciones,
                       toleradas=()) -> None:
    """Regla del dueño (2-oct-2026): en el tenant dueño de SAE ningún producto
    ACTIVO se guarda sin la clave SAE de cada una de sus unidades. Un producto
    sin clave es una partida que no sale en el masivo y que alguien descubre
    con la factura a medias. Quien llama decide si aplica (activo, y en la
    edición sólo si se tocó lo que define las claves).

    `toleradas`: las unidades que el producto YA tenía sin clave antes de una
    edición. No bloquean — si no, llenar un hueco (la base, desde el aviso de
    Remisiones) se rechazaría por OTRO hueco que nadie tocó —; lo que se
    prohíbe es perder una clave o sumar una unidad sin ella.

    Los demás tenants no tienen SAE: ahí la clave ni se pide."""
    if not es_duenio_de_sae(ctx.tenant_id):
        return
    faltan = [u for u in _unidades_sin_clave_sae(unidad_base, clave_sae, presentaciones)
              if u not in set(toleradas)]
    if faltan:
        raise HTTPException(status_code=422,
                            detail=f"Falta la clave SAE de {', '.join(faltan)}")


def _sin_sae_no_hay_altas(ctx: AuthContext, altas) -> None:
    """`altas_sae` sólo existe para el tenant dueño de SAE: el escritor no
    atiende a otro, y su alta se quedaría en la cola hasta caducar."""
    if altas and not es_duenio_de_sae(ctx.tenant_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Esta empresa no tiene SAE conectado")


_DUP = "Ya existe un producto con ese SKU"


def _validate_fks(db: Session, *, categoria_id, esquema_impuesto_id) -> None:
    ensure_fk(db, CategoriaProducto, categoria_id, "categoria_id")
    ensure_fk(db, EsquemaImpuesto, esquema_impuesto_id, "esquema_impuesto_id")


def _max_sku_num(db: Session) -> int:
    """Highest fully-numeric SKU for the tenant (legacy alphanumeric SKUs are
    ignored). The import loop increments from here without re-querying."""
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
    return mx


def _next_sku(db: Session) -> str:
    """Next 8-digit sequential SKU for the tenant."""
    return f"{_max_sku_num(db) + 1:08d}"


def _similar_filter(query, term: str):
    """Match a term against nombre, sku, descripción y sinónimos (ilike)."""
    like = f"%{term}%"
    return query.filter(
        Producto.nombre.ilike(like)
        | Producto.sku.ilike(like)
        | Producto.descripcion.ilike(like)
        | func.array_to_string(Producto.sinonimos, " ").ilike(like)
    )


def _con_descripcion_sat(db: Session, filas: list[Producto]) -> None:
    """Adjunta la descripción oficial del catálogo SAT a cada producto.

    En UNA consulta para toda la página: hacerlo por renglón serían 50 viajes a
    la base solo para poder leer la clave. La clave que no exista en el catálogo
    se queda sin descripción (no se inventa: el listado la muestra vacía)."""
    claves = {f.clave_sat for f in filas if f.clave_sat}
    if not claves:
        return
    desc = {
        c.clave: c.descripcion
        for c in db.query(SatClaveProdServ).filter(SatClaveProdServ.clave.in_(claves)).all()
    }
    for f in filas:
        f.clave_sat_descripcion = desc.get(f.clave_sat or "")


@router.get("", response_model=Page[ProductoOut])
def list_productos(
    q: Optional[str] = Query(default=None, max_length=254),
    categoria_id: Optional[UUID] = Query(default=None),
    esquema_impuesto_id: Optional[UUID] = Query(default=None),
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
    if esquema_impuesto_id is not None:
        query = query.filter(Producto.esquema_impuesto_id == esquema_impuesto_id)
    if activo is not None:
        query = query.filter(Producto.activo.is_(activo))
    query = query.order_by(Producto.nombre.asc())
    return paginate(query, ProductoOut, limit, offset,
                    preparar=lambda rows: _con_descripcion_sat(db, rows))


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


def _cand_producto(p: Producto, score: int, origen: str) -> Candidato:
    """Producto → candidato, para las rutas que NO pasan por `buscar` (el cruce
    por código del archivo, y la sugerencia de IA)."""
    return Candidato(
        producto_id=p.id, sku=p.sku, nombre=p.nombre, score=score, origen=origen,
        presentaciones=p.presentaciones or {},
        presentacion_default=p.presentacion_default,
        unidad_base=p.unidad_base,
        categoria_id=p.categoria_id,
        esquema_impuesto_id=p.esquema_impuesto_id,
    )


def _mapas_catalogo(db: Session) -> tuple[dict, dict]:
    """id → nombre de categoría, id → código de esquema. Incluye las dadas de
    baja: un producto puede seguir apuntando a una categoría inactiva y la
    pantalla debe poder nombrarla."""
    cats = {
        c.id: c.nombre
        for c in db.query(CategoriaProducto).filter(CategoriaProducto.deleted_at.is_(None)).all()
    }
    esqs = {
        e.id: e.codigo
        for e in db.query(EsquemaImpuesto).filter(EsquemaImpuesto.deleted_at.is_(None)).all()
    }
    return cats, esqs


def _candidato_out(c, cats_por_id: dict, esquemas_por_id: dict) -> CandidatoOut:
    """Candidato → salida. La categoría y el esquema del producto existente
    viajan SIEMPRE: quien vincula hereda los suyos, y la pantalla los muestra
    en vez de un "Sin categoría" que engaña."""
    return CandidatoOut(
        producto_id=c.producto_id, sku=c.sku, nombre=c.nombre,
        score=c.score, origen=c.origen,
        presentaciones=c.presentaciones,
        presentacion_default=c.presentacion_default,
        unidad_base=c.unidad_base,
        categoria_id=c.categoria_id,
        categoria_nombre=(cats_por_id.get(c.categoria_id) or ""),
        esquema_impuesto_id=c.esquema_impuesto_id,
        esquema_codigo=(esquemas_por_id.get(c.esquema_impuesto_id) or ""),
        clave_sat=c.clave_sat,
        unidad_sat=c.unidad_sat,
    )


@router.post("/match", response_model=list[MatchResultOut])
def match_productos(
    payload: MatchIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Cruza textos libres (tecleados/pegados) contra el catálogo: exacto → alias
    aprendido → difuso, y opcionalmente IA para los que no resuelvan.

    Con `cliente_id` (y `sucursal_id`) el vocabulario de ese cliente va primero,
    como `alias_cliente`: es como el bot pregunta qué producto es lo que pidió un
    cliente sin escogerlo él."""
    if payload.cliente_id is not None and not ctx.cliente_permitido(payload.cliente_id):
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    if payload.usar_ia:
        # La rama IA manda el catálogo completo como contexto (cuesta dinero).
        enforce(f"producto-ia:{ctx.tenant_id}", 120, 3600)
    catalogo = productos_activos(db, ctx.tenant_id)   # una sola carga para todos los textos
    aliases = alias_del_tenant(db, ctx.tenant_id)     # idem: sin esto era un SELECT por texto
    aliases_cli = alias_de_cliente(db, ctx.tenant_id, payload.cliente_id, payload.sucursal_id)
    norms = normalizar_catalogo(catalogo)   # y sin esto, O(textos × productos)
    cats_por_id, esquemas_por_id = _mapas_catalogo(db)
    resultados: list[dict] = []
    sin_match: list[str] = []
    for texto in payload.textos:
        cands = buscar(db, ctx.tenant_id, texto, limit=payload.limit, prods=catalogo,
                       aliases=aliases, aliases_cliente=aliases_cli, norms=norms)
        resultados.append({
            "texto": texto,
            "candidatos": [_candidato_out(c, cats_por_id, esquemas_por_id) for c in cands],
        })
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
                r["candidatos"] = [
                    _candidato_out(_cand_producto(p, 85, "ia"), cats_por_id, esquemas_por_id)
                ]
    return resultados


def _norm_sku(v: str) -> str:
    """'PIÑA -FRUT-350' cruza con 'PINA-FRUT-350': mayúsculas, sin acentos ni
    espacios. Misma tolerancia que el cruce por clave de la bandeja de OC."""
    import unicodedata

    s = unicodedata.normalize("NFKD", v or "").encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in s.upper() if ch.isalnum() or ch == "-")


def _num_txt(v) -> str:
    """Los números del lector vienen float; la línea los viaja como texto (el
    front los formatea). 10.0 debe llegar como '10', no como '10.0'. El cero
    llega como VACÍO — misma regla que el pegado (`cero_vacio`): un $0 del
    OCR no es un precio, y como texto '0' bloqueaba la cotización automática."""
    if v is None or (isinstance(v, (int, float)) and float(v) == 0):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _cruzar_filas(
    db: Session, ctx: AuthContext, filas: list[dict], *, usar_ia: bool
) -> list[dict]:
    """Filas ya parseadas —pegadas de Excel o leídas de un archivo— → líneas con
    sus candidatos del catálogo: exacto → alias → difuso → IA.

    Cuando la fila trae `clave` (las requisiciones de SAE la traen) se cruza
    primero contra el SKU: es exacta y no hay que adivinar por descripción.
    """
    catalogo = productos_activos(db, ctx.tenant_id)   # una sola carga para todas las filas
    aliases = alias_del_tenant(db, ctx.tenant_id)     # idem: sin esto era un SELECT por fila
    norms = normalizar_catalogo(catalogo)   # y sin esto, O(filas × productos)
    cats_por_id, esquemas_por_id = _mapas_catalogo(db)
    por_sku: dict[str, Producto] = {}
    for p in catalogo:
        if p.sku:
            por_sku.setdefault(_norm_sku(p.sku), p)

    resultados: list[dict] = []
    sin_match: list[str] = []
    for f in filas:
        # Varios candidatos para poblar el desplegable Match IA (el front muestra ≥80%).
        cands = buscar(db, ctx.tenant_id, f["producto"], limit=8, prods=catalogo, aliases=aliases, norms=norms)
        # La clave del documento manda sobre la descripción: si existe en el
        # catálogo va al frente como cruce exacto, sin duplicarse más abajo.
        clave = _norm_sku(f.get("clave") or "")
        exacto = por_sku.get(clave) if clave else None
        if exacto is not None:
            cands = [_cand_producto(exacto, 100, "exacto")] + [
                c for c in cands if c.producto_id != exacto.id
            ]
        resultados.append({
            "texto": f["producto"],
            "cantidad": f["cantidad"],
            "precio": f["precio"],
            "presentacion": f["presentacion"],
            "candidatos": [_candidato_out(c, cats_por_id, esquemas_por_id) for c in cands],
        })
        if not cands:
            sin_match.append(f["producto"])

    # IA solo para los que ni exacto/alias/difuso resolvieron (sinónimos regionales).
    if usar_ia and sin_match:
        ia = sugerir_con_ia(db, ctx.tenant_id, sin_match)
        pids = {pid for pid in ia.values() if pid}
        prods = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(pids)).all()} if pids else {}
        for r in resultados:
            pid = ia.get(r["texto"])
            if not r["candidatos"] and pid and pid in prods:
                p = prods[pid]
                r["candidatos"] = [
                    _candidato_out(_cand_producto(p, 85, "ia"), cats_por_id, esquemas_por_id)
                ]
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
    return _cruzar_filas(db, ctx, filas, usar_ia=payload.usar_ia)


@router.post("/parse-archivo", response_model=list[LineaPegadaOut])
def parse_archivo(
    archivo: UploadFile = File(...),
    usar_ia: bool = Form(default=True),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """PDF, foto o Excel de una orden del cliente → las MISMAS líneas que
    «Pegar de Excel», ya cruzadas contra el catálogo.

    Reusa el lector del bot (`requisicion_parse.leer_documento`, el mismo del
    /cotizador): un PDF pasa por los acomodos deterministas de pdfplumber y
    solo cae a la IA de visión si no sacaron partidas; una foto o un Excel van
    directo a la IA. Declarado antes de /{producto_id} para no capturarse como
    UUID."""
    from ...services import requisicion_parse

    # Leer un documento cuesta dinero de API (visión) — mismo tope por tenant
    # que el cotizador, que corre exactamente este lector.
    enforce(f"producto-archivo:{ctx.tenant_id}", 60, 3600)
    _MAX = 10 * 1024 * 1024
    data = archivo.file.read(_MAX + 1)
    if not data:
        raise HTTPException(status_code=422, detail="El archivo llegó vacío")
    if len(data) > _MAX:
        raise HTTPException(status_code=422, detail="El archivo no debe exceder 10 MB")
    try:
        doc = requisicion_parse.leer_documento(data, archivo.filename or "documento")
    except Exception as exc:   # documento corrupto, o la IA no disponible
        logger.warning("parse-archivo: no se pudo leer %s: %s", archivo.filename, exc)
        raise HTTPException(status_code=422, detail=f"No se pudo leer el archivo: {str(exc)[:160]}")

    filas: list[dict] = []
    for it in doc.get("items") or []:
        # La descripción es lo que se cruza; una partida sin ella no es línea.
        descripcion = (it.get("descripcion") or "").strip()
        if not descripcion:
            continue
        filas.append({
            "producto": descripcion,
            "cantidad": _num_txt(it.get("cantidad")),
            "precio": _num_txt(it.get("costo_unitario")),
            "presentacion": (it.get("unidad") or "").strip(),
            "clave": (it.get("clave") or "").strip(),
        })
    if not filas:
        raise HTTPException(
            status_code=422,
            detail="No se encontraron partidas legibles en el documento",
        )
    return _cruzar_filas(db, ctx, filas, usar_ia=usar_ia)


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
    envenenaría el cruce de todo el negocio.

    Con `cliente_id` el alias es vocabulario privado de ese cliente: crear o
    reapuntar EN ese alcance no toca el global, así que basta la lectura — es
    exactamente el caso del bot enseñando "chile tampico"→serrano para un solo
    cliente sin poder envenenar a los demás."""
    ensure_fk(db, Producto, payload.producto_id, "producto_id")
    presentacion = _presentacion_del_alias(db, payload.producto_id, payload.presentacion)
    unidad_oc = normalizar_unidad_oc(payload.unidad_oc)
    if payload.cliente_id is not None:
        ensure_fk(db, Cliente, payload.cliente_id, "cliente_id")
        # Sin validar la sucursal, el INSERT viola su FK dentro del savepoint de
        # `aprender_alias` y el endpoint contestaba 201 sin haber guardado nada.
        ensure_fk(db, Sucursal, payload.sucursal_id, "sucursal_id")
        aprender_alias(
            db, ctx.tenant_id, payload.texto, payload.producto_id,
            cliente_id=payload.cliente_id, sucursal_id=payload.sucursal_id,
            unidad_oc=unidad_oc, presentacion=presentacion,
            origen="MANUAL", user_id=ctx.user_id,
        )
        return {"ok": True}
    existente = (
        db.query(ProductoAlias)
        .filter(
            ProductoAlias.alias_normalizado == normalizar(payload.texto),
            ProductoAlias.cliente_id.is_(None),
            ProductoAlias.sucursal_id.is_(None),
            (ProductoAlias.unidad_oc == unidad_oc) if unidad_oc
            else ProductoAlias.unidad_oc.is_(None),
        )
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
    aprender_alias(db, ctx.tenant_id, payload.texto, payload.producto_id,
                   unidad_oc=unidad_oc, presentacion=presentacion,
                   origen="MANUAL", user_id=ctx.user_id)
    return {"ok": True}


def _presentacion_del_alias(db: Session, producto_id: UUID, presentacion: Optional[str]) -> Optional[str]:
    """La unidad del sistema de un renglón del vocabulario, validada: tiene que
    ser una que el producto venda (si no, la partida entraría en una unidad sin
    precio ni clave de SAE)."""
    if not presentacion or not presentacion.strip():
        return None
    prod = db.get(Producto, producto_id)
    if not presentacion_declarada(prod, presentacion):
        raise HTTPException(
            status_code=422,
            detail=f"{prod.nombre if prod else 'El producto'} no se vende en {presentacion.strip().upper()}",
        )
    return presentacion.strip().upper()


@router.get("/vocabulario", response_model=Page[VocabularioOut])
def vocabulario(
    q: str = Query("", max_length=120),
    cliente_id: Optional[UUID] = None,
    solo_global: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """La tabla puente: qué escribe cada cliente = qué producto es.

    Declarada ANTES de `/{producto_id}` para no capturarse como UUID.

    `q` busca en los dos lados a la vez —el texto del cliente y el nombre o SKU
    del producto— porque la pregunta real es «enséñame todo lo que tenga que ver
    con limón», y quien pregunta no sabe de qué lado está lo que busca.
    """
    base = (
        db.query(ProductoAlias, Producto, Cliente.legal_name, Sucursal.nombre)
        .join(Producto, Producto.id == ProductoAlias.producto_id)
        .outerjoin(Cliente, Cliente.id == ProductoAlias.cliente_id)
        .outerjoin(Sucursal, Sucursal.id == ProductoAlias.sucursal_id)
        .filter(Producto.deleted_at.is_(None))
    )
    if solo_global:
        base = base.filter(ProductoAlias.cliente_id.is_(None))
    elif cliente_id is not None:
        base = base.filter(ProductoAlias.cliente_id == cliente_id)
    termino = (q or "").strip()
    if termino:
        like = f"%{normalizar(termino)}%"
        base = base.filter(
            ProductoAlias.alias_normalizado.ilike(like)
            | Producto.nombre.ilike(f"%{termino}%")
            | Producto.sku.ilike(f"%{termino}%")
        )
    total = base.order_by(None).count()
    filas = (
        base.order_by(Producto.nombre, ProductoAlias.cliente_id.nullsfirst(), ProductoAlias.alias)
        .offset(offset).limit(limit).all()
    )
    # Qué marcar en rojo. Que un texto lleve a otro producto para OTRO cliente
    # no es un conflicto: la cascada lo resuelve sola (cliente+sucursal >
    # cliente > global) y que cada quien llame a lo suyo a su manera es lo
    # normal — marcarlo teñía media pantalla de ámbar sin nada que arreglar.
    # Conflicto de verdad es que DOS reglas del MISMO alcance lleven a
    # productos distintos: ahí nadie decide y gana el orden físico de la tabla.
    # Aparte se cuenta, sólo en la fila GLOBAL, a cuántos clientes les dijeron
    # otra cosa: es la pista de un global mal apuntado (el «AJO» que apuntaba a
    # ajonjolí), que se cobra en los clientes que aún no tienen regla propia.
    normas = {a.alias_normalizado for a, _, _, _ in filas}
    ambiguos: set[str] = set()
    pisado: dict[tuple[str, UUID], int] = {}
    if normas:
        # El filtro por tenant va explícito: en las pruebas la BD corre como
        # superusuario (RLS apagado) y sin él se cuelan los alias de otro tenant.
        hermanos = (
            db.query(
                ProductoAlias.alias_normalizado,
                ProductoAlias.cliente_id,
                ProductoAlias.sucursal_id,
                ProductoAlias.producto_id,
                ProductoAlias.unidad_oc,
            )
            .filter(
                ProductoAlias.tenant_id == ctx.tenant_id,
                ProductoAlias.alias_normalizado.in_(normas),
            )
            .all()
        )
        por_alcance: dict[tuple, set[UUID]] = {}
        por_cliente: dict[str, list[tuple[UUID, UUID]]] = {}
        for norm, cli_id, suc_id, prod_id, uoc in hermanos:
            # «SANDIA | KG» y «SANDIA | PZ» son reglas distintas, no un choque.
            por_alcance.setdefault((norm, cli_id, suc_id, uoc), set()).add(prod_id)
            if cli_id is not None:
                por_cliente.setdefault(norm, []).append((cli_id, prod_id))
        ambiguos = {llave[0] for llave, prods in por_alcance.items() if len(prods) > 1}
        for a, _, _, _ in filas:
            if a.cliente_id is not None:
                continue
            otros = {
                cli for cli, prod in por_cliente.get(a.alias_normalizado, [])
                if prod != a.producto_id
            }
            if otros:
                pisado[(a.alias_normalizado, a.producto_id)] = len(otros)
    return Page[VocabularioOut](
        items=[
            VocabularioOut(
                id=a.id, texto=a.alias, origen=a.origen,
                producto_id=p.id, producto_sku=p.sku, producto_nombre=p.nombre,
                cliente_id=a.cliente_id, cliente_nombre=cli,
                sucursal_id=a.sucursal_id, sucursal_nombre=suc,
                unidad_oc=a.unidad_oc, presentacion=a.presentacion,
                producto_presentaciones=_unidades_de(p),
                clave_sae=(clave_sae_de(p, a.presentacion) or None) if a.presentacion else None,
                ambiguo=a.alias_normalizado in ambiguos,
                pisado_por=pisado.get((a.alias_normalizado, a.producto_id), 0),
            )
            for a, p, cli, suc in filas
        ],
        total=total, limit=limit, offset=offset,
    )


def _unidades_de(p: Producto) -> list[str]:
    """Las unidades que vende el producto, la base primero."""
    base = p.unidad_base or p.presentacion_default
    resto = [k for k in (p.presentaciones or {}) if k != base]
    return ([base] if base else []) + resto


def _alias_editable(db: Session, alias_id: UUID, ctx: AuthContext) -> ProductoAlias:
    """El alias, si esta persona puede tocarlo.

    Misma línea que `crear_alias` (decisión 2026-07-29): el vocabulario de UN
    cliente lo corrige quien captura, porque equivocarse ahí solo afecta a ese
    cliente y es el flujo natural del día. El GLOBAL lo heredan todos —incluido
    el cliente nuevo que todavía no tiene vocabulario propio— así que moverlo
    exige gestión de productos.
    """
    alias = get_or_404(db, ProductoAlias, alias_id)
    if alias.cliente_id is None and _WRITE not in ctx.permissions and not ctx.is_owner:
        raise HTTPException(
            status_code=403,
            detail=("Ese texto lo usan todos los clientes; cambiarlo requiere el "
                    "permiso de gestión de productos"),
        )
    return alias


@router.patch("/alias/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
def reapuntar_alias(
    alias_id: UUID,
    payload: AliasReapuntarIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Corrige el renglón: el texto, el producto y/o la sucursal del cliente."""
    alias = _alias_editable(db, alias_id, ctx)
    if payload.producto_id is not None:
        ensure_fk(db, Producto, payload.producto_id, "producto_id")
        if payload.producto_id != alias.producto_id and "presentacion" not in payload.model_fields_set:
            # La unidad era del producto anterior: si el nuevo no la vende, se quita.
            if not presentacion_declarada(db.get(Producto, payload.producto_id), alias.presentacion):
                alias.presentacion = None
        alias.producto_id = payload.producto_id
    if "presentacion" in payload.model_fields_set:
        alias.presentacion = _presentacion_del_alias(db, alias.producto_id, payload.presentacion)
    if "unidad_oc" in payload.model_fields_set:
        alias.unidad_oc = normalizar_unidad_oc(payload.unidad_oc)
    if payload.texto is not None:
        norm = normalizar(payload.texto)[:254]
        if not norm:
            raise HTTPException(status_code=422, detail="El texto no puede quedar vacío")
        alias.alias = payload.texto.strip()[:254]
        alias.alias_normalizado = norm
    if "sucursal_id" in payload.model_fields_set and payload.sucursal_id != alias.sucursal_id:
        if alias.cliente_id is None:
            raise HTTPException(
                status_code=422,
                detail="Una regla de todos los clientes no se puede acotar a una sucursal",
            )
        if payload.sucursal_id is not None:
            ensure_fk(db, Sucursal, payload.sucursal_id, "sucursal_id")
            if not es_sucursal_de(db, payload.sucursal_id, alias.cliente_id):
                raise HTTPException(
                    status_code=422, detail="Esa sucursal no surte a este cliente",
                )
        alias.sucursal_id = payload.sucursal_id
    alias.origen = "MANUAL"        # lo decidió una persona: deja de ser importado
    # El índice único es (tenant, cliente, sucursal, texto normalizado): al
    # reescribir el texto se puede chocar con otro renglón del MISMO alcance.
    flush_or_conflict(db, detail="Ese texto (con esa unidad de la orden) ya está en el vocabulario de ese alcance")
    return None


@router.delete("/alias/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
def borrar_alias(
    alias_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Quita el texto del vocabulario. Se borra de verdad: un alias no es un
    documento, y dejarlo en `deleted_at` obligaría a filtrarlo en cada cruce."""
    db.delete(_alias_editable(db, alias_id, ctx))
    db.flush()
    return None


# ─── Importación masiva (plantilla o lista de precios del cliente) ───────────
# Declaradas antes de /{producto_id} para no capturarse como UUID.

_TABULARES = ("xlsx", "xls", "csv")

# Palabras que no discriminan producto (calificativos genéricos de listas).
_STOP_CRUCE = {
    "de", "del", "la", "el", "los", "las", "a", "en", "con", "y", "o",
    "primera", "granel", "natural", "fresco", "fresca", "limpio", "limpia",
    "kg", "kilo", "kilogramo", "pza", "pieza", "pz", "lt", "litro",
}

# Transformaciones del producto: si el CANDIDATO las trae y el archivo NO, es
# otro producto ("CHILE JALAPEÑO" fresco ≠ "CHILE JALAPEÑO PICADOS EN LATA").
_PROCESADO = {
    "picado", "picados", "polvo", "pulpa", "molido", "molida", "lata",
    "enlatado", "enlatada", "escabeche", "adobado", "adobados", "congelado",
    "congelada", "jugo", "deshidratado", "deshidratada", "seco", "seca",
    "japones", "caramelizado", "caramelizada", "tostado", "tostada",
}


def _misma_palabra(a: str, b: str) -> bool:
    """¿Dos tokens son la MISMA palabra escrita distinto?

    Singular/plural y erratas cortas no cambian de producto: "ACELGAS" es la
    "ACELGA" del catálogo. Sin esto, la s de más contaba como token extra y la
    fila se iba a "crear producto nuevo" teniendo el suyo al 96%."""
    if a == b:
        return True
    corto, largo = sorted((a, b), key=len)
    if len(corto) >= 4 and largo in (corto + "s", corto + "es"):
        return True
    return len(corto) >= 5 and fuzz.ratio(a, b) >= 90


def _cruce_confiable(nombre_archivo: str, nombre_candidato: str) -> bool:
    """¿Se puede auto-sugerir VINCULAR un candidato difuso?

    El scorer de búsqueda (token_set_ratio) ignora tokens sobrantes: tecleando
    "ajo" debe aparecer "AJO EN POLVO". Pero al IMPORTAR esa dirección liga mal.
    Dos reglas:
    1. Si el nombre del ARCHIVO trae tokens con contenido que el candidato no
       tiene (es MÁS específico: "AJO EN POLVO" vs "AJO"), no se auto-vincula.
       "Tener" se mide con `_misma_palabra`: el plural no hace un token nuevo.
    2. Si el CANDIDATO trae una transformación que el archivo no pide
       ("CHILE JALAPEÑO" vs "...PICADOS 215 GR LATA"), tampoco.
    En ambos casos la fila queda como "crear" con los candidatos visibles para
    que el usuario decida en un clic."""
    qa = {t for t in normalizar(nombre_archivo).split() if t not in _STOP_CRUCE}
    pa = {t for t in normalizar(nombre_candidato).split() if t not in _STOP_CRUCE}
    # Tokens del archivo que el candidato no tiene ni siquiera como variante.
    if any(not any(_misma_palabra(t, c) for c in pa) for t in qa):
        return False
    sobran = {c for c in pa if not any(_misma_palabra(c, t) for t in qa)}
    return not (sobran & _PROCESADO)      # transformación solo en el candidato


@router.get("/plantilla-importacion")
def plantilla_importacion(
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Descarga la plantilla oficial .xlsx para el alta masiva de productos."""
    data = generar_plantilla()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="plantilla-productos.xlsx"'},
    )


@router.post("/importar-preview", response_model=ImportPreviewOut)
def importar_preview(
    archivo: UploadFile = File(...),
    cliente_id: Optional[UUID] = Form(default=None),
    cliente_ids: list[UUID] = Form(default=[]),
    usar_ia: bool = Form(default=True),
    mapeo: Optional[str] = Form(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Archivo → filas estructuradas + cruce contra el catálogo. NO crea nada.

    Plantilla (trae columna NOMBRE/PRODUCTO) → parseo determinista. Cualquier
    otro acomodo, PDF o foto → IA. Cada fila regresa candidatos del catálogo
    (exacto → alias → difuso) para vincular en vez de duplicar; si se manda
    `cliente_id`, también se marca lo que ese cliente ya tiene vinculado."""
    _MAX = 10 * 1024 * 1024
    data = archivo.file.read(_MAX + 1)
    if len(data) > _MAX:
        raise HTTPException(status_code=422, detail="El archivo no debe exceder 10 MB")
    if not data:
        raise HTTPException(status_code=422, detail="El archivo está vacío")
    filename = archivo.filename or "archivo"
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()

    # Mapeo columna→campo corregido por el usuario en la pantalla de columnas.
    mapeo_cols: Optional[dict[int, str]] = None
    if mapeo:
        try:
            crudo = json.loads(mapeo)
            mapeo_cols = {
                int(k): str(v) for k, v in dict(crudo).items()
                if isinstance(v, str) and v.strip()
            }
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="Mapeo de columnas inválido")

    columnas_info: dict = {"columnas": [], "campos": []}
    requiere_mapeo = False
    try:
        if ext in _TABULARES:
            # Las columnas del archivo SIEMPRE se exponen: es lo que el usuario
            # revisa antes de importar nada. Si no se reconoció ninguna columna
            # como la descripción del producto, se devuelven igual (sin filas)
            # para que él indique cuál es — sin ese camino, justo los archivos
            # con encabezados ajenos se quedaban sin pantalla de mapeo.
            columnas_info = analizar_columnas(data, filename) or columnas_info
            try:
                filas = parsear_plantilla(data, filename, mapeo_cols)
            except ImportProductosError:
                # Con mapeo del usuario pero sin columna de nombre: se regresa a
                # la pantalla de mapeo con el aviso, en vez de un callejón 422.
                if mapeo_cols is None:
                    raise
                filas = None
            if mapeo_cols is not None:
                # Lo que el usuario corrigió manda sobre lo detectado, pero solo
                # lo que el parseo realmente aceptó (campo válido, sin repetir).
                validos = {v for v, _ in CAMPOS_MAPEABLES}
                aceptado: dict[int, str] = {}
                for i, campo in sorted(mapeo_cols.items()):
                    if campo in validos and campo not in aceptado.values():
                        aceptado[i] = campo
                for c in columnas_info["columnas"]:
                    c["campo"] = aceptado.get(c["indice"], "")
            if filas is None:
                requiere_mapeo = True
                filas = []
        else:
            filas = None
        formato = "plantilla" if filas is not None else "ia"
        if filas is None:
            if not usar_ia:
                raise ImportProductosError(
                    "El archivo no coincide con la plantilla. Activa la opción de IA "
                    "o descarga la plantilla y captura ahí los productos."
                )
            # La rama IA cuesta dinero: mismo límite que el cruce por IA.
            enforce(f"producto-ia:{ctx.tenant_id}", 120, 3600)
            filas = extraer_con_ia(data, filename)
    except ImportProductosError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Renglones con datos que se descartaron por no traer nombre.
    filas_sin_nombre = int(filas[0].pop("_saltadas", 0)) if filas else 0

    # Cruce contra el catálogo, una sola carga para todas las filas.
    catalogo = productos_activos(db, ctx.tenant_id)
    aliases = alias_del_tenant(db, ctx.tenant_id)     # una sola carga: sin esto, un SELECT por fila
    norms = normalizar_catalogo(catalogo)   # y sin esto, O(filas × productos)
    por_sku = {p.sku.strip().upper(): p for p in catalogo if p.sku}
    por_id = {p.id: p for p in catalogo}

    # Validación en lote contra el catálogo SAT oficial (única fuente).
    claves_archivo = {f["clave_sat"] for f in filas if f.get("clave_sat")}
    claves_ok = {
        c for (c,) in db.query(SatClaveProdServ.clave)
        .filter(SatClaveProdServ.clave.in_(claves_archivo)).all()
    } if claves_archivo else set()
    unidades_archivo = {(f.get("unidad_sat") or "").upper() for f in filas if f.get("unidad_sat")}
    unidades_ok = {
        c.upper() for (c,) in db.query(SatClaveUnidad.clave)
        .filter(SatClaveUnidad.clave.in_(unidades_archivo)).all()
    } if unidades_archivo else set()

    # Categorías y esquemas del tenant (objetos: el preview resuelve el id de
    # cada fila para que el usuario los vea y cambie en su propia columna).
    cats_lista = db.query(CategoriaProducto).filter(CategoriaProducto.deleted_at.is_(None)).all()
    cats_por_nombre = {normalizar(c.nombre): c for c in cats_lista}
    cats_por_id = {c.id: c.nombre for c in cats_lista}
    cats_tenant = set(cats_por_nombre)
    esquemas_lista = (
        db.query(EsquemaImpuesto)
        .filter(EsquemaImpuesto.deleted_at.is_(None), EsquemaImpuesto.activo.is_(True))
        .all()
    )
    esquemas_por_id = {e.id: e.codigo for e in esquemas_lista}
    esquemas_por_clave: dict = {}
    for e in esquemas_lista:
        for k in (e.codigo, e.nombre):
            if k:
                esquemas_por_clave.setdefault(normalizar(k), e)
    esquemas_tenant = set(esquemas_por_clave)

    # Lo que los clientes elegidos YA tienen vinculado. Con VARIOS clientes un
    # mismo código puede apuntar a productos distintos según el cliente: en ese
    # caso NO se auto-sugiere ninguno (sugerir el del primer cliente que salga
    # del SELECT ligaría la fila al producto equivocado). Solo manda el código
    # cuando todos los clientes que lo usan coinciden en el producto.
    ids_clientes = list(dict.fromkeys(([cliente_id] if cliente_id else []) + list(cliente_ids)))
    productos_vinculados: set = set()
    codigo_a_productos: dict[str, set] = {}
    if ids_clientes:
        for cid in ids_clientes:
            ensure_fk(db, Cliente, cid, "cliente_id")
        for pc in (
            db.query(ProductoCliente)
            .filter(ProductoCliente.cliente_id.in_(ids_clientes))
            .all()
        ):
            productos_vinculados.add(pc.producto_id)
            cod = (pc.codigo_cliente or "").strip().upper()
            if cod:
                codigo_a_productos.setdefault(cod, set()).add(pc.producto_id)
    # Códigos sin ambigüedad entre los clientes elegidos.
    pc_por_codigo = {
        cod: next(iter(pids)) for cod, pids in codigo_a_productos.items() if len(pids) == 1
    }

    out: list[ImportFilaPreview] = []
    # nombre/código normalizado → (primera fila, su precio)
    vistos: dict[str, tuple[int, str]] = {}
    for n, f in enumerate(filas, start=1):
        codigo = (f.get("codigo") or "").strip()
        sugerido = None
        ya_vinculado = False

        # Duplicados DENTRO del archivo (listas reales repiten renglones): se
        # marca la repetición para que la UI la omita por default. Mismo nombre
        # con OTRA unidad no es duplicado (KG vs PZ = dos presentaciones). Si la
        # repetida trae OTRO precio, se marca el conflicto — que lo vea un
        # humano, no se descarta un precio distinto en silencio.
        claves = [f"n:{normalizar(f['nombre'])}|{f.get('unidad') or ''}"] + (
            [f"c:{codigo.upper()}"] if codigo else [])
        previa = next((vistos[k] for k in claves if k in vistos), None)
        duplicada_de = previa[0] if previa else None
        precio_distinto = bool(
            previa and previa[1] and (f.get("precio") or "")
            and previa[1] != f.get("precio")
        )
        for k in claves:
            vistos.setdefault(k, (n, f.get("precio") or ""))

        # 1) El código del cliente ya está vinculado → ese producto, sin dudar.
        pid_por_codigo = pc_por_codigo.get(codigo.upper()) if codigo else None
        if pid_por_codigo is not None:
            sugerido = pid_por_codigo
            ya_vinculado = True

        # 2) El código coincide EXACTO con un SKU interno.
        if sugerido is None and codigo and codigo.upper() in por_sku:
            sugerido = por_sku[codigo.upper()].id

        # 3) Cruce por nombre (exacto → alias → difuso). Los difusos solo se
        #    auto-sugieren si el cruce es confiable en la dirección de importar.
        cands = buscar(db, ctx.tenant_id, f["nombre"], limit=5, prods=catalogo, aliases=aliases, norms=norms)
        if sugerido is None and cands and cands[0].score >= 80:
            top = cands[0]
            if top.origen in ("exacto", "alias") or _cruce_confiable(f["nombre"], top.nombre):
                sugerido = top.producto_id
        if sugerido is not None and not ya_vinculado:
            ya_vinculado = sugerido in productos_vinculados
        # El sugerido puede venir por CÓDIGO (pasos 1 y 2) y no estar entre los
        # candidatos, que salen del cruce por NOMBRE: es justo el caso "ROMA" ↔
        # "JITOMATE SALADETTE". Sin él en la lista, la pantalla no sabe a qué
        # vinculó — enseñaba "Sin categoría", no ofrecía la equivalencia de la
        # presentación nueva y el alta terminaba con "1 MANOJO = 1 KILO".
        if sugerido is not None and not any(c.producto_id == sugerido for c in cands):
            prod_cod = por_id.get(sugerido)
            if prod_cod is not None:
                cands = [_cand_producto(prod_cod, 100, "exacto"), *cands]

        # Variante nueva: cruza a un producto existente pero con una unidad que
        # el producto aún no maneja ("Cilantro" KILO ← fila en MANOJO).
        nueva_presentacion = False
        unidad_fila = f.get("unidad") or ""
        prod_sug = por_id.get(sugerido) if sugerido else None
        if prod_sug is not None and unidad_fila:
            conocidas = {(prod_sug.unidad_base or "").upper()} | {
                str(k).upper() for k in (prod_sug.presentaciones or {})
            }
            nueva_presentacion = unidad_fila.upper() not in conocidas

        clave_f = f.get("clave_sat") or ""
        unidad_sat_f = (f.get("unidad_sat") or "").upper()
        out.append(ImportFilaPreview(
            fila=n,
            nombre=f["nombre"],
            codigo=codigo,
            descripcion=f.get("descripcion") or "",
            unidad=unidad_fila,
            precio=f.get("precio") or "",
            clave_sat=clave_f,
            unidad_sat=unidad_sat_f,
            codigo_barras=f.get("codigo_barras") or "",
            categoria=f.get("categoria") or "",
            categoria_id=(cats_por_nombre.get(normalizar(f.get("categoria") or "")).id
                          if normalizar(f.get("categoria") or "") in cats_por_nombre else None),
            esquema=f.get("esquema") or "",
            esquema_id=(esquemas_por_clave.get(normalizar(f.get("esquema") or "")).id
                        if normalizar(f.get("esquema") or "") in esquemas_por_clave else None),
            esquema_origen=("archivo" if normalizar(f.get("esquema") or "") in esquemas_por_clave else ""),
            baja=(f.get("estatus") or "") in ("BAJA", "INACTIVO", "B"),
            clave_sat_valida=(clave_f in claves_ok) if clave_f else None,
            unidad_sat_valida=(unidad_sat_f in unidades_ok) if unidad_sat_f else None,
            nueva_presentacion=nueva_presentacion,
            producto_id=sugerido,
            candidatos=[_candidato_out(c, cats_por_id, esquemas_por_id) for c in cands],
            ya_vinculado=ya_vinculado,
            duplicada_de=duplicada_de,
            precio_distinto=precio_distinto,
        ))

    # Dos filas distintas del archivo vinculadas al MISMO producto ("PIMIENTA" y
    # "PIMIENTA BLANCA" → un solo bote): se marca la segunda para revisarla —
    # si se importan ambas, la última pisa el código/nombre/precio del cliente.
    # EXCEPTO cuando la segunda trae otra unidad: eso es una variante legítima
    # del mismo producto (Cilantro KILO + Cilantro MANOJO), no un choque.
    primera_por_producto: dict = {}
    for fila in out:
        if fila.producto_id is None or fila.duplicada_de is not None:
            continue
        previa = primera_por_producto.get(fila.producto_id)
        if previa is not None and not fila.nueva_presentacion:
            fila.mismo_producto_que = previa
        elif previa is None:
            primera_por_producto[fila.producto_id] = fila.fila

    # Meta para las preguntas en LOTE del wizard.
    activas = [f for f in out if f.duplicada_de is None and not f.baja]
    # Categorías del archivo cruzadas contra las del tenant: "ABARROTE" del
    # archivo y "Abarrotes" del sistema son la misma — se reusa, no se duplica.
    nombres_archivo = sorted({f.categoria for f in activas if f.categoria})
    cat_matches = match_categorias(nombres_archivo, cats_lista)
    for m in cat_matches:
        if m["categoria_id"] is not None:
            # La fila apunta a la categoría existente que le corresponde.
            for f in out:
                if f.categoria == m["nombre_archivo"] and f.categoria_id is None:
                    f.categoria_id = m["categoria_id"]
    cats_nuevas = sorted({m["nombre_archivo"] for m in cat_matches if m["es_nueva"]})
    esq_no_encontrados = sorted({
        f.esquema for f in activas
        if f.esquema and normalizar(f.esquema) not in esquemas_tenant
    })
    return ImportPreviewOut(
        formato=formato,
        filas=out,
        columnas=[ImportColumnaOut(**c) for c in columnas_info["columnas"]],
        campos_mapeables=columnas_info["campos"],
        requiere_mapeo=requiere_mapeo,
        filas_sin_nombre=filas_sin_nombre,
        faltan_clave_sat=sum(1 for f in activas if not f.clave_sat),
        faltan_unidad_sat=sum(1 for f in activas if not f.unidad_sat),
        categorias_nuevas=cats_nuevas,
        categorias_match=[ImportCategoriaMatch(**m) for m in cat_matches],
        esquemas_no_encontrados=esq_no_encontrados,
        filas_sin_esquema=sum(1 for f in activas if f.esquema_id is None),
        tiene_precios=any(f.precio for f in activas),
    )


@router.post("/sugerir-categoria-batch", response_model=list[SugerenciaCategoriaOut])
def sugerir_categoria_batch(
    payload: SugerirCategoriaBatchIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Qué categoría le toca a cada producto NUEVO, entre las que ya usa el
    negocio (las de /categorias). Una sola llamada de IA para todo el lote;
    nunca inventa categorías nuevas."""
    if payload.usar_ia:
        enforce(f"producto-ia:{ctx.tenant_id}", 120, 3600)
    categorias = (
        db.query(CategoriaProducto)
        .filter(CategoriaProducto.deleted_at.is_(None), CategoriaProducto.activo.is_(True))
        .all()
    )
    if not categorias:
        raise HTTPException(
            status_code=422,
            detail="No hay categorías dadas de alta para sugerir. Créalas en Categorías.",
        )
    productos = [
        {"nombre": str(p.get("nombre", "")).strip(), "clave_sat": str(p.get("clave_sat", "")).strip()}
        for p in payload.productos
        if str(p.get("nombre", "")).strip()
    ]
    if not productos:
        raise HTTPException(status_code=422, detail="Sin productos que clasificar")
    return [
        SugerenciaCategoriaOut(**s)
        for s in sugerir_categorias(productos, categorias, usar_ia=payload.usar_ia)
    ]


@router.post("/sugerir-esquema-batch", response_model=list[SugerenciaEsquemaOut])
def sugerir_esquema_batch(
    payload: SugerirEsquemaBatchIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Qué esquema de impuesto le toca a cada producto, entre los que YA tiene
    el negocio. Sin esquema el CFDI saldría sin IVA/IEPS, así que la
    importación no debe dejar productos en blanco.

    Primero reglas fiscales mexicanas por clave SAT/nombre (alimentos IVA 0%,
    limpieza y plásticos 16%, refrescos y botanas con IEPS) y, para lo que no
    resuelvan, una sola llamada de IA que ELIGE entre los esquemas del tenant."""
    if payload.usar_ia:
        enforce(f"producto-ia:{ctx.tenant_id}", 120, 3600)
    esquemas = (
        db.query(EsquemaImpuesto)
        .filter(EsquemaImpuesto.deleted_at.is_(None), EsquemaImpuesto.activo.is_(True))
        .all()
    )
    if not esquemas:
        raise HTTPException(
            status_code=422,
            detail="No hay esquemas de impuesto dados de alta. Crea al menos uno "
                   "(por ejemplo IVA 0% para alimentos e IVA 16%) en Esquemas de impuesto.",
        )
    productos = [
        {
            "nombre": str(p.get("nombre", "")).strip(),
            "clave_sat": str(p.get("clave_sat", "")).strip(),
            "categoria": str(p.get("categoria", "")).strip(),
        }
        for p in payload.productos
        if str(p.get("nombre", "")).strip()
    ]
    if not productos:
        raise HTTPException(status_code=422, detail="Sin productos que evaluar")
    return [
        SugerenciaEsquemaOut(**s)
        for s in sugerir_esquemas(productos, esquemas, usar_ia=payload.usar_ia)
    ]


@router.post("/sugerir-sat-batch", response_model=list[SugerenciaSatOut])
def sugerir_sat_batch(
    payload: SugerirSatBatchIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Pregunta 1/2 del wizard: claves y unidades SAT sugeridas para N productos
    en una pasada. SOLO devuelve claves que existen en el catálogo SAT oficial
    cargado en el sistema (la IA elige entre candidatos del catálogo; sin IA,
    gana el mejor candidato por texto; sin candidatos, la genérica 01010101)."""
    enforce(f"producto-ia:{ctx.tenant_id}", 120, 3600)
    productos = [
        {"nombre": str(p.get("nombre", "")).strip(), "unidad": str(p.get("unidad", "")).strip()}
        for p in payload.productos
        if str(p.get("nombre", "")).strip()
    ]
    if not productos:
        raise HTTPException(status_code=422, detail="Sin productos que sugerir")
    return [SugerenciaSatOut(**s) for s in sugerir_batch(db, productos)]


def _codigo_categoria_libre(nombre: str, usados: set[str]) -> str:
    """Código de categoría único SIN consultar la base (los usados ya vienen
    precargados). Misma regla que services/categoria_codigo."""
    base = slugify_codigo(nombre)
    if base not in usados:
        return base
    for n in range(2, 1000):
        sufijo = str(n)
        cand = (base[: 10 - len(sufijo)] or "CAT") + sufijo
        if cand not in usados:
            return cand
    return base


# Unidad de venta capturada → clave SAT de unidad (fallback razonable).
_UNIDAD_A_SAT = {
    "KILO": "KGM", "GRAMO": "GRM", "LITRO": "LTR", "MILILITRO": "MLT",
    "PIEZA": "H87", "CAJA": "XBX", "PAQUETE": "XPK", "BOLSA": "XBG",
    "COSTAL": "XSA", "BULTO": "XSA", "DOCENA": "DPC",
    "MANOJO": "H87", "MALLA": "XBG", "REJA": "XBX", "ATADO": "H87",
}


@router.post("/importar", response_model=ImportResultOut)
def importar_productos(
    payload: ImportIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Aplica el preview confirmado. Ver `_ejecutar_import`.

    Se intenta en modo LOTE (un flush para todo: con 500 productos es la
    diferencia entre 2 segundos y varios minutos contra una base en la nube).
    Si el lote choca contra una restricción, se rehace fila por fila —dentro de
    un savepoint, para no perder el scope de RLS de la transacción— y así se
    puede señalar CUÁL fila falló conservando las demás."""
    try:
        with db.begin_nested():
            return _ejecutar_import(db, ctx, payload, aislar_filas=False)
    except IntegrityError as exc:
        logger.warning("import en lote chocó, se rehace fila por fila: %s", exc)
        # El rollback al savepoint no saca de la sesión los objetos que quedaron
        # pendientes: sin esto, el reintento los volvería a insertar.
        db.expunge_all()
        return _ejecutar_import(db, ctx, payload, aislar_filas=True)


def _bitacora_import(
    db: Session,
    ctx: AuthContext,
    *,
    origen: str,
    archivo_nombre: Optional[str],
    cliente_ids: list,
    detalle: dict,
    **numeros: int,
) -> None:
    """Deja el renglón de quién importó qué (ver `models/import_productos_log`).

    Va en su propio savepoint y se traga sus errores a propósito: la bitácora
    existe para contestar una pregunta forense, no para tumbar un alta que sí
    funcionó. Si se cae, se pierde el renglón y queda el aviso en el log del
    servidor — nunca el import.
    """
    try:
        with db.begin_nested():
            db.add(ImportProductosLog(
                id=uuid4(),
                tenant_id=ctx.tenant_id,
                origen=origen,
                user_id=ctx.user_id,
                archivo_nombre=(archivo_nombre or "").strip()[:254] or None,
                cliente_ids=[str(c) for c in cliente_ids],
                detalle=detalle,
                **numeros,
            ))
    except SQLAlchemyError:
        logger.warning("no se pudo escribir la bitácora de importación", exc_info=True)


def _lista_global_de(db: Session, cliente_id):
    """La lista asignada al cliente «para todo el país»: sin sucursal, sin serie
    y sin proyecto. Es la que el wizard de importación considera «su lista»."""
    fila = (
        db.query(ListaAsignacion.lista_id)
        .filter(
            ListaAsignacion.cliente_id == cliente_id,
            ListaAsignacion.sucursal_id.is_(None),
            ListaAsignacion.serie_id.is_(None),
            ListaAsignacion.proyecto_id.is_(None),
        )
        .order_by(ListaAsignacion.created_at.desc())
        .first()
    )
    return fila[0] if fila else None


def _ejecutar_import(
    db: Session, ctx: AuthContext, payload: ImportIn, *, aislar_filas: bool
) -> ImportResultOut:
    """Aplica el preview confirmado: crea productos nuevos (SKU automático),
    vincula existentes y, si la lista es de UN cliente, guarda su código/nombre
    en el catálogo del cliente (+ alias para el cruce) y opcionalmente sus
    precios en la lista de precios del cliente."""
    # Uno o VARIOS clientes (grupo/cadena que comparte la misma lista): los
    # códigos/nombres/presentaciones del archivo se guardan para cada uno.
    ids_clientes = list(dict.fromkeys(
        ([payload.cliente_id] if payload.cliente_id else []) + list(payload.cliente_ids)
    ))
    clientes = [get_or_404(db, Cliente, cid) for cid in ids_clientes]

    lista_id = None
    lista_nombre_out = None
    if payload.guardar_precios:
        lista_id = payload.lista_id
        # Un solo cliente CON lista → la suya (comportamiento de siempre). Se
        # lee de su asignación GLOBAL, que es donde vive desde la 0050.
        if lista_id is None and len(clientes) == 1:
            lista_id = _lista_global_de(db, clientes[0].id)
        if lista_id is None and (payload.lista_nombre or "").strip():
            # Crear la lista aquí mismo (menos pasos): código único desde el
            # nombre. Se asigna a los clientes elegidos que NO tengan lista
            # (jamás se pisa una lista ya asignada).
            nombre_lista = payload.lista_nombre.strip()
            base = "".join(ch for ch in nombre_lista.upper() if ch.isalnum())[:20] or "LISTA"
            codigo_lista = base
            n = 2
            while db.query(ListaPrecios.id).filter(ListaPrecios.codigo == codigo_lista).first():
                sufijo = str(n)
                codigo_lista = base[: 20 - len(sufijo)] + sufijo
                n += 1
            lista = ListaPrecios(tenant_id=ctx.tenant_id, codigo=codigo_lista, nombre=nombre_lista)
            db.add(lista)
            db.flush()
            lista_id = lista.id
            for cli in clientes:
                if _lista_global_de(db, cli.id) is None:
                    db.add(ListaAsignacion(
                        tenant_id=ctx.tenant_id, lista_id=lista.id, cliente_id=cli.id
                    ))
        if lista_id is None:
            raise HTTPException(
                status_code=422,
                detail="Para guardar precios se necesita una lista de precios "
                       "(elige una, dale un nombre a la nueva, o asigna una al cliente)",
            )
        ensure_fk(db, ListaPrecios, lista_id, "lista_id")
        lista_nombre_out = db.query(ListaPrecios.nombre).filter(ListaPrecios.id == lista_id).scalar()
    if payload.esquema_default_id is not None:
        ensure_fk(db, EsquemaImpuesto, payload.esquema_default_id, "esquema_default_id")

    # Categorías y esquemas del tenant por nombre/código normalizado (para
    # resolver las columnas CATEGORIA y ESQUEMA del archivo).
    cats_por_nombre: dict[str, CategoriaProducto] = {}
    for c in db.query(CategoriaProducto).filter(CategoriaProducto.deleted_at.is_(None)).all():
        cats_por_nombre.setdefault(normalizar(c.nombre), c)
    esquemas_por_clave: dict[str, EsquemaImpuesto] = {}
    for e in db.query(EsquemaImpuesto).filter(EsquemaImpuesto.deleted_at.is_(None)).all():
        for k in (e.codigo, e.nombre):
            if k:
                esquemas_por_clave.setdefault(normalizar(k), e)

    # Precargas: todo lo que el bucle consultaba POR FILA. Con 500 productos
    # eso eran miles de viajes a la base — contra una base en la nube, minutos.
    ids_clientes_sel = [c.id for c in clientes]
    pc_previos: dict[tuple, ProductoCliente] = {}
    if ids_clientes_sel:
        # Solo genéricas: el import no conoce plazas, y sin este filtro una
        # fila por sucursal pisaría a la genérica en el dict (o al revés).
        for pc in (
            db.query(ProductoCliente)
            .filter(
                ProductoCliente.cliente_id.in_(ids_clientes_sel),
                ProductoCliente.sucursal_id.is_(None),
            )
            .all()
        ):
            pc_previos[(pc.cliente_id, pc.producto_id)] = pc
    precios_previos: dict[tuple, Precio] = {}
    if lista_id is not None:
        for pr in db.query(Precio).filter(Precio.lista_id == lista_id).all():
            precios_previos[(pr.producto_id, pr.presentacion, pr.cantidad_minima)] = pr
    alias_previos = alias_del_tenant(db, ctx.tenant_id)   # {alias_normalizado: producto_id}
    # Productos a vincular: se traen de una sola vez.
    ids_vincular = {f.producto_id for f in payload.filas
                    if f.accion == "vincular" and f.producto_id}
    prods_vincular = {
        p.id: p for p in db.query(Producto)
        .filter(Producto.id.in_(ids_vincular), Producto.deleted_at.is_(None)).all()
    } if ids_vincular else {}
    # Códigos de categoría ya usados (para generar los de las nuevas sin
    # consultar la base por cada una).
    codigos_cat = {
        c for (c,) in db.query(CategoriaProducto.codigo)
        .filter(CategoriaProducto.tenant_id == ctx.tenant_id).all()
    }
    # Ids válidos precargados: `ensure_fk` consultaba la base DOS veces por fila
    # y, en modo lote, no veía las categorías creadas en esta misma pasada.
    cats_validas = {
        c for (c,) in db.query(CategoriaProducto.id)
        .filter(CategoriaProducto.deleted_at.is_(None)).all()
    }
    esquemas_validos = {
        e for (e,) in db.query(EsquemaImpuesto.id)
        .filter(EsquemaImpuesto.deleted_at.is_(None)).all()
    }
    # Destino de las filas que se crean sin categoría: se resuelve UNA vez para
    # todo el lote (crearla por fila sería un viaje a la base por renglón).
    cat_default = categoria_sin_categorizar(db, ctx.tenant_id)
    cats_validas.add(cat_default.id)

    # SKUs secuenciales sin re-consultar el máximo en cada fila.
    siguiente_sku = _max_sku_num(db)

    # En modo lote los objetos se escriben en TRES fases, porque estas tablas
    # no declaran relationship y SQLAlchemy no deduce el orden de sus FK:
    # categorías → productos → (catálogo del cliente, alias, precios).
    nuevas_cats: list = []
    nuevos_prods: list = []
    nuevos_deps: list = []

    def _nuevo(obj, fase: list) -> None:
        """Encola el objeto en su fase (lote) o lo escribe ya (modo aislado)."""
        if aislar_filas:
            db.add(obj)
        else:
            fase.append(obj)

    def _esquema_de(fila) -> Optional[UUID]:
        """Qué esquema le toca a la fila: id explícito → código/nombre del
        archivo → default del lote. Una sola definición, para que la revisión
        previa y el alta no puedan diferir."""
        if fila.esquema_impuesto_id is not None:
            return fila.esquema_impuesto_id
        if (fila.esquema or "").strip():
            esq = esquemas_por_clave.get(normalizar(fila.esquema))
            if esq is not None:
                return esq.id
        return payload.esquema_default_id

    # ── Nada se da de alta sin esquema de impuesto ──────────────────────────
    # Sin esquema el producto nace sin IVA/IEPS y el CFDI sale mal; el alta
    # manual tampoco lo permite. Se revisa ANTES de escribir nada y se nombran
    # TODAS las filas que faltan: fallar en la 57 dejaría el lote a medias y
    # obligaría a descubrirlas de una en una. Las que se VINCULAN no entran —
    # conservan el esquema del producto que ya existe.
    sin_esquema = [
        (n, (fila.nombre or fila.sku or "").strip())
        for n, fila in enumerate(payload.filas, start=1)
        if fila.accion == "crear" and _esquema_de(fila) is None
    ]
    if sin_esquema:
        muestra = "; ".join(f"fila {n}: {d}" for n, d in sin_esquema[:8])
        if len(sin_esquema) > 8:
            muestra += f"; …y {len(sin_esquema) - 8} más"
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(sin_esquema)} productos nuevos no traen esquema de impuesto y "
                f"no se pueden dar de alta así ({muestra}). Asígnaselo en la columna "
                "«Esquema» o elige un esquema para todo el lote antes de aprobar."
            ),
        )

    resultados_filas: list[ImportProductoResultado] = []

    creados = vinculados = alias_guardados = precios_guardados = omitidos = 0
    categorias_creadas = presentaciones_agregadas = 0
    errores: list[ImportErrorFila] = []

    for n, fila in enumerate(payload.filas, start=1):
        if fila.accion == "omitir":
            omitidos += 1
            continue
        antes = (creados, vinculados, alias_guardados, precios_guardados,
                 categorias_creadas, presentaciones_agregadas)
        antes_filas = len(resultados_filas)
        try:
            with (db.begin_nested() if aislar_filas else nullcontext()):
                unidad_fila = normalizar_unidad(fila.unidad_base or "")

                # Categoría: id explícito → nombre del archivo (existente o
                # creada si el lote lo pidió) → sin categoría.
                categoria_id = fila.categoria_id
                if categoria_id is None and (fila.categoria or "").strip():
                    cat = cats_por_nombre.get(normalizar(fila.categoria))
                    # Al vincular nunca se usa la categoría de la fila (el
                    # producto conserva la suya), así que crearla dejaba
                    # categorías vacías en el catálogo.
                    if cat is None and payload.crear_categorias and fila.accion != "vincular":
                        codigo_cat = _codigo_categoria_libre(fila.categoria, codigos_cat)
                        codigos_cat.add(codigo_cat)
                        cat = CategoriaProducto(
                            id=uuid4(), tenant_id=ctx.tenant_id,
                            codigo=codigo_cat, nombre=fila.categoria.strip(),
                        )
                        _nuevo(cat, nuevas_cats)
                        cats_por_nombre[normalizar(cat.nombre)] = cat
                        cats_validas.add(cat.id)
                        categorias_creadas += 1
                    if cat is not None:
                        categoria_id = cat.id
                if categoria_id is None and fila.accion != "vincular":
                    categoria_id = cat_default.id

                # Esquema: id explícito → código/nombre del archivo → default
                # del lote (ver `_esquema_de`, que es lo que ya se revisó arriba).
                esquema_id = _esquema_de(fila)

                if fila.accion == "vincular":
                    if fila.producto_id is None:
                        raise ValueError("Falta el producto a vincular")
                    prod = prods_vincular.get(fila.producto_id)
                    if prod is None:
                        raise ValueError("Producto a vincular no encontrado")
                    # Variante nueva del MISMO producto (Cilantro KILO ← MANOJO):
                    # se agrega la presentación con su factor y su unidad SAT.
                    conocidas = {(prod.unidad_base or "").upper()} | {
                        str(k).upper() for k in (prod.presentaciones or {})
                    }
                    if unidad_fila and unidad_fila.upper() not in conocidas:
                        factor = float(fila.presentacion_factor or 1)
                        sat = (fila.unidad_sat or "").strip().upper() \
                            or _UNIDAD_A_SAT.get(unidad_fila, prod.unidad_sat)
                        prod.presentaciones = {
                            **(prod.presentaciones or {}),
                            unidad_fila: {"factor": factor, "sat": sat},
                        }
                        presentaciones_agregadas += 1
                    vinculados += 1
                else:  # crear
                    if categoria_id is not None and categoria_id not in cats_validas:
                        raise ValueError("categoria_id inválido o fuera de alcance")
                    if esquema_id is not None and esquema_id not in esquemas_validos:
                        raise ValueError("esquema_impuesto_id inválido o fuera de alcance")
                    unidad_base = unidad_fila or "KILO"
                    sku = (fila.sku or "").strip()
                    if not sku:
                        siguiente_sku += 1
                        sku = f"{siguiente_sku:08d}"
                    prod = Producto(
                        id=uuid4(),   # sin RETURNING: permite INSERT por lotes
                        tenant_id=ctx.tenant_id,
                        sku=sku,
                        nombre=fila.nombre.strip().upper(),
                        descripcion=(fila.descripcion or "").strip() or None,
                        categoria_id=categoria_id,
                        esquema_impuesto_id=esquema_id,
                        clave_sat=(fila.clave_sat or "").strip() or "01010101",
                        unidad_sat=(fila.unidad_sat or "").strip().upper()
                                   or _UNIDAD_A_SAT.get(unidad_base, "KGM"),
                        unidad_base=unidad_base,
                        presentaciones={unidad_base: 1},
                        presentacion_default=unidad_base,
                        codigo_barras=(fila.codigo_barras or "").strip() or None,
                        activo=fila.activo,
                        created_by=ctx.user_id,
                        updated_by=ctx.user_id,
                    )
                    _nuevo(prod, nuevos_prods)
                    creados += 1

                # Catálogo del cliente: su código → NoIdentificacion, su nombre
                # → Descripcion del CFDI, su presentación → cómo le vende. Se
                # guarda para CADA cliente elegido (una lista, varios clientes).
                codigo_c = (fila.codigo_cliente or "").strip() or None
                nombre_c = (fila.nombre_cliente or "").strip() or None
                if clientes and (codigo_c or nombre_c):
                    for cliente in clientes:
                        clave_pc = (cliente.id, prod.id)
                        pc = pc_previos.get(clave_pc)
                        if pc is None:
                            pc = ProductoCliente(
                                id=uuid4(), tenant_id=ctx.tenant_id,
                                cliente_id=cliente.id, producto_id=prod.id,
                                created_by=ctx.user_id,
                            )
                            _nuevo(pc, nuevos_deps)
                            pc_previos[clave_pc] = pc
                        pc.codigo_cliente = codigo_c
                        pc.nombre_cliente = nombre_c
                        pc.updated_by = ctx.user_id
                        if unidad_fila:
                            pc.presentacion = unidad_fila
                        alias_guardados += 1
                    # El cruce también aprende el nombre (una vez, es del tenant).
                    if nombre_c:
                        norm_alias = normalizar(nombre_c)[:254]
                        if norm_alias and norm_alias not in alias_previos:
                            _nuevo(ProductoAlias(
                                id=uuid4(), tenant_id=ctx.tenant_id,
                                producto_id=prod.id, alias=nombre_c.strip()[:254],
                                alias_normalizado=norm_alias, origen="IMPORT",
                                created_by=ctx.user_id,
                            ), nuevos_deps)
                            alias_previos[norm_alias] = prod.id

                # Precio → lista indicada, EN la presentación de la fila (el
                # precio del MANOJO no es el del KILO).
                if lista_id is not None and fila.precio is not None:
                    presentacion = unidad_fila or prod.presentacion_default or prod.unidad_base or "KILO"
                    clave_precio = (prod.id, presentacion, 1)
                    precio_row = precios_previos.get(clave_precio)
                    if precio_row is None:
                        precio_row = Precio(
                            id=uuid4(), tenant_id=ctx.tenant_id, lista_id=lista_id,
                            producto_id=prod.id, presentacion=presentacion,
                            precio_unitario=fila.precio, cantidad_minima=1,
                        )
                        _nuevo(precio_row, nuevos_deps)
                        precios_previos[clave_precio] = precio_row
                    else:
                        precio_row.precio_unitario = fila.precio
                    precios_guardados += 1

                # El último paso usa esto para guardar el catálogo del cliente
                # sin volver a subir el archivo.
                resultados_filas.append(ImportProductoResultado(
                    fila=n, producto_id=prod.id,
                    codigo=(fila.codigo_cliente or fila.sku or "").strip(),
                    nombre=fila.nombre.strip(),
                    presentacion=unidad_fila or "",
                ))

                if aislar_filas:
                    db.flush()   # el savepoint de ESTA fila la aísla del resto
        except (IntegrityError, ValueError) as exc:
            # Esta fila no cuenta: se deshacen sus sumas.
            (creados, vinculados, alias_guardados, precios_guardados,
             categorias_creadas, presentaciones_agregadas) = antes
            del resultados_filas[antes_filas:]
            detalle = str(exc)
            if isinstance(exc, IntegrityError):
                detalle = _DUP if "uq_producto_tenant_sku" in str(exc.orig) else "Registro duplicado"
            errores.append(ImportErrorFila(fila=n, error=detalle))

    if not aislar_filas:
        # Tres viajes para TODO el lote, en orden de dependencia. Los INSERT de
        # cada fase se agrupan porque los id se asignaron en Python (no hace
        # falta RETURNING por fila). Si algo choca, el endpoint reintenta
        # aislando cada fila para poder señalar cuál.
        for fase in (nuevas_cats, nuevos_prods, nuevos_deps):
            if fase:
                db.add_all(fase)
            db.flush()

    # Quién corrió esta pasada, con qué archivo y para qué clientes. Es lo que
    # faltó el 16-sep-2026 para atribuir un producto recién importado.
    _bitacora_import(
        db, ctx,
        origen="IMPORT",
        archivo_nombre=payload.archivo_nombre,
        cliente_ids=ids_clientes,
        filas_enviadas=len(payload.filas),
        productos_creados=creados,
        productos_vinculados=vinculados,
        catalogo_guardado=alias_guardados,
        precios_guardados=precios_guardados,
        filas_con_error=len(errores),
        detalle={
            "omitidos": omitidos,
            "categorias_creadas": categorias_creadas,
            "presentaciones_agregadas": presentaciones_agregadas,
            "lista_id": str(lista_id) if lista_id else None,
            "lista_nombre": lista_nombre_out,
            # El lote chocó y se rehizo fila por fila (ver `importar_productos`).
            "aislado": aislar_filas,
        },
    )

    return ImportResultOut(
        creados=creados, vinculados=vinculados, alias_guardados=alias_guardados,
        precios_guardados=precios_guardados, omitidos=omitidos,
        categorias_creadas=categorias_creadas,
        presentaciones_agregadas=presentaciones_agregadas,
        lista_id=lista_id, lista_nombre=lista_nombre_out,
        productos=resultados_filas,
        errores=errores,
    )


@router.post("/catalogo-cliente-batch", response_model=CatalogoClienteBatchOut)
def catalogo_cliente_batch(
    payload: CatalogoClienteBatchIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Guarda de golpe el código/nombre/presentación que usan uno o varios
    clientes para una lista de productos — el último paso de la importación,
    cuando el usuario ya decidió de quién era la lista.

    Escribe por lotes (una consulta para leer lo existente, un flush al final):
    con 500 productos × 2 clientes, hacerlo uno por uno serían miles de viajes.
    """
    clientes = [get_or_404(db, Cliente, cid) for cid in payload.cliente_ids]
    ids_prod = {i.producto_id for i in payload.items}
    validos = {
        p for (p,) in db.query(Producto.id)
        .filter(Producto.id.in_(ids_prod), Producto.deleted_at.is_(None)).all()
    }
    # Solo filas GENÉRICAS: la importación de listas escribe la clave general
    # del cliente; las filas por sucursal (claves por plaza, caso EHMO) se
    # administran en su catálogo y aquí ni se leen ni se pisan.
    previos = {
        (pc.cliente_id, pc.producto_id): pc
        for pc in db.query(ProductoCliente)
        .filter(
            ProductoCliente.cliente_id.in_([c.id for c in clientes]),
            ProductoCliente.producto_id.in_(ids_prod),
            ProductoCliente.sucursal_id.is_(None),
        )
        .all()
    }
    alias_previos = alias_del_tenant(db, ctx.tenant_id)
    # Los alias que ya tienen estos clientes, para no chocar con su índice único.
    alias_cliente_previos = {
        (a.cliente_id, a.alias_normalizado)
        for a in db.query(ProductoAlias)
        .filter(ProductoAlias.cliente_id.in_([c.id for c in clientes]))
        .all()
    }
    nuevos: list = []
    guardados = 0
    for item in payload.items:
        if item.producto_id not in validos:
            continue
        codigo = (item.codigo or "").strip() or None
        nombre = (item.nombre or "").strip() or None
        if not codigo and not nombre:
            continue
        for cliente in clientes:
            clave = (cliente.id, item.producto_id)
            pc = previos.get(clave)
            if pc is None:
                pc = ProductoCliente(
                    id=uuid4(), tenant_id=ctx.tenant_id,
                    cliente_id=cliente.id, producto_id=item.producto_id,
                    created_by=ctx.user_id,
                )
                nuevos.append(pc)
                previos[clave] = pc
            pc.codigo_cliente = codigo
            pc.nombre_cliente = nombre
            pc.updated_by = ctx.user_id
            if item.presentacion:
                pc.presentacion = item.presentacion
            guardados += 1
        # El cruce aprende el nombre del cliente: GLOBAL si el texto es nuevo
        # (le sirve a todos); con alcance del cliente si ya apunta a OTRO
        # producto — el mismo texto puede significar cosas distintas por cliente
        # y el global no se pisa.
        if nombre:
            norm_alias = normalizar(nombre)[:254]
            previo = alias_previos.get(norm_alias) if norm_alias else None
            if norm_alias and previo is None:
                nuevos.append(ProductoAlias(
                    id=uuid4(), tenant_id=ctx.tenant_id, producto_id=item.producto_id,
                    alias=nombre[:254], alias_normalizado=norm_alias,
                    origen="IMPORT", created_by=ctx.user_id,
                ))
                alias_previos[norm_alias] = item.producto_id
            elif norm_alias and previo != item.producto_id:
                for cliente in clientes:
                    if (cliente.id, norm_alias) in alias_cliente_previos:
                        continue
                    nuevos.append(ProductoAlias(
                        id=uuid4(), tenant_id=ctx.tenant_id, producto_id=item.producto_id,
                        cliente_id=cliente.id,
                        alias=nombre[:254], alias_normalizado=norm_alias,
                        origen="IMPORT", created_by=ctx.user_id,
                    ))
                    alias_cliente_previos.add((cliente.id, norm_alias))
    if nuevos:
        db.add_all(nuevos)
    db.flush()
    # Este paso —no el de /importar— es donde el wizard elige de QUIÉN es la
    # lista, así que es el único renglón que puede contestar "a qué clientes se
    # les escribió el catálogo".
    _bitacora_import(
        db, ctx,
        origen="CATALOGO",
        archivo_nombre=payload.archivo_nombre,
        cliente_ids=payload.cliente_ids,
        filas_enviadas=len(payload.items),
        catalogo_guardado=guardados,
        detalle={"productos": len(validos)},
    )
    return CatalogoClienteBatchOut(
        clientes=len(clientes), productos=len(validos), guardados=guardados
    )


@router.post("", response_model=ProductoOut, status_code=status.HTTP_201_CREATED)
def create_producto(
    payload: ProductoCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    _sin_sae_no_hay_altas(ctx, payload.altas_sae)
    # Nada nace sin esquema de impuesto: sin él el producto no lleva IVA/IEPS y
    # su CFDI sale mal. Va aquí y no en el schema porque `ProductoBase` lo
    # comparte la SALIDA, y los productos viejos que aún tienen el hueco deben
    # poder leerse para justamente poder arreglarlos.
    if payload.esquema_impuesto_id is None:
        raise HTTPException(
            status_code=422,
            detail="Elige el esquema de impuesto: sin él el producto no lleva IVA y su factura saldría mal",
        )
    # La categoría también se elige (regla del dueño, 2-oct-2026): el alta desde
    # la pantalla ya no cae sola en «Sin categorizar». Esa caída se queda en las
    # puertas sin pantalla (importación, alta desde SAE), donde no hay a quién
    # preguntarle.
    if payload.categoria_id is None:
        raise HTTPException(status_code=422, detail="Elige la categoría del producto")
    _validate_fks(
        db,
        categoria_id=payload.categoria_id,
        esquema_impuesto_id=payload.esquema_impuesto_id,
    )
    data = payload.model_dump()
    forzar = data.pop("forzar", False)
    data.pop("altas_sae", None)            # no es columna: se encola abajo
    _clave_sae_limpia(db, ctx, data)
    if data.get("nombre"):
        data["nombre"] = data["nombre"].strip().upper()   # nombres siempre en mayúsculas
    if data.get("activo", True):
        _exigir_claves_sae(ctx, data.get("unidad_base"), data.get("clave_sae"),
                           data.get("presentaciones"))

    # El SKU interno lo genera el servidor. Un SKU con guiones o letras es un
    # código DEL CLIENTE (CILA-FRUT-145) colándose como producto nuevo — la
    # regla del catálogo multicliente lo manda a producto_clientes, no aquí.
    sku = (data.get("sku") or "").strip()
    if sku and not sku.isdigit():
        raise HTTPException(
            status_code=422,
            detail=(
                "El SKU interno es numérico y lo genera el servidor; los códigos "
                "del cliente se registran en el catálogo del cliente, no como SKU"
            ),
        )
    if not sku:
        data["sku"] = _next_sku(db)   # auto-generate when blank

    # Detector de duplicados en el alta individual (la importación ya lo tenía):
    # con candidato fuerte, el alta exige decidir — "es el mismo, vincular" o
    # forzar la creación a sabiendas. Aquí es donde nacían los cilantros ×6.
    if not forzar and data.get("nombre"):
        cands = [
            c for c in buscar(db, ctx.tenant_id, data["nombre"], limit=5)
            if c.score >= 88
        ]
        if cands:
            cats_por_id, esquemas_por_id = _mapas_catalogo(db)
            raise HTTPException(
                status_code=409,
                detail={
                    "mensaje": (
                        "Ya hay productos muy parecidos en el catálogo. Vincula el "
                        "existente o repite con forzar=true si de verdad es otro."
                    ),
                    "candidatos": [
                        _candidato_out(c, cats_por_id, esquemas_por_id).model_dump(mode="json")
                        for c in cands
                    ],
                },
            )

    obj = Producto(
        **data, tenant_id=ctx.tenant_id,
        created_by=ctx.user_id, updated_by=ctx.user_id,
    )
    db.add(obj)
    flush_or_conflict(db, detail=_DUP)
    # Las claves nuevas se piden en la MISMA transacción: si el alta en la
    # cola no se puede encolar, el producto tampoco se guarda (y al revés).
    altas = _encolar_altas_del_producto(db, ctx, obj, payload.altas_sae)
    db.refresh(obj)
    obj.altas_sae = altas
    return obj


@router.post("/impuestos", response_model=list[ImpuestoDeClaveOut])
def impuestos_por_clave(
    payload: ImpuestosPorClaveIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """La fiscalidad de un lote de claves, en una sola pregunta.

    El bot resuelve impuestos contra SAE clave por clave (INVE.CVE_ESQIMPU →
    IMPU.IMPUESTO4) desde seis funciones distintas. Esto contesta lo mismo con
    el esquema del producto, que es el único cerebro fiscal de este lado
    (`services/fiscal.calcular_linea_producto` lee estos mismos campos).

    La clave se busca por `clave_sae` y, si no cae, por `sku`: el bot llama con
    lo que traiga el documento y no siempre es lo mismo.

    Una clave que no existe aquí NO se calla: vuelve con `encontrado: false`.
    Callarla sería contestar «0% de IVA» a un producto que nadie conoce, que es
    la peor respuesta posible — el bot tiene que poder distinguir «no lleva
    IVA» de «no sé quién es»."""
    # Se devuelve la clave TAL COMO LLEGÓ, no normalizada: quien pregunta arma
    # su diccionario con lo que mandó, y devolverle otra cosa lo obliga a
    # normalizar igual que aquí para volver a encontrarla.
    pedidas = [str(c) for c in payload.claves if str(c or "").strip()]
    if not pedidas:
        return []
    arriba = {c.strip().upper() for c in pedidas}
    q = (
        db.query(Producto, EsquemaImpuesto)
        .outerjoin(EsquemaImpuesto, EsquemaImpuesto.id == Producto.esquema_impuesto_id)
        .filter(Producto.tenant_id == ctx.tenant_id, Producto.deleted_at.is_(None),
                or_(func.upper(func.btrim(Producto.clave_sae)).in_(arriba),
                    func.upper(func.btrim(Producto.sku)).in_(arriba)))
    )
    por_clave: dict = {}
    for prod, esq in q.all():
        for llave in ((prod.clave_sae or "").strip().upper(), (prod.sku or "").strip().upper()):
            if llave and llave in arriba:
                por_clave.setdefault(llave, (prod, esq))
    out = []
    for c in pedidas:
        par = por_clave.get(c.strip().upper())
        if par is None:
            out.append(ImpuestoDeClaveOut(clave=c, encontrado=False))
            continue
        prod, esq = par
        out.append(ImpuestoDeClaveOut(
            clave=c, encontrado=True, producto_id=prod.id, nombre=prod.nombre,
            esquema=(esq.codigo if esq else ""),
            # sin esquema manda lo que trae el producto: es lo que hace el
            # cálculo fiscal de este lado, y las dos respuestas tienen que ser
            # la misma o el bot y la factura dirían cosas distintas
            iva=(esq.iva_tasa if esq else (prod.iva_tasa or 0)),
            ieps=(esq.ieps_tasa if esq else (prod.ieps_tasa or 0)),
            tipo_ieps=(esq.tipo_ieps if esq else "TASA"),
            ieps_cuota=(esq.ieps_cuota if esq else 0),
            iva_exento=bool(esq.iva_exento) if esq else False,
            objeto_imp=prod.objeto_imp or "02",
            activo=bool(prod.activo),
        ))
    return out


# OJO con el orden: estas rutas van ANTES de GET /{producto_id} — FastAPI casa
# en orden de declaración y "alta-sae" parsearía como UUID (422).
# ── Altas y cambios en SAE ───────────────────────────────────────────────────
# Dar de alta o cambiar un producto allá funciona por SOLICITUD: aquí queda
# pedida, un escritor la reclama y reporta qué hizo en cada empresa. Ese
# escritor es el Facturador (`services/sae_escritura.py`, 26-sep-2026) cuando
# su reloj está encendido; si no, el conector del bot, que sólo sabe de altas.
# Nunca los dos: ver `reclamar_alta_sae`.
#
# LA REGLA QUE MANDA EN TODO ESTE CAMINO: nunca se reintenta una escritura a
# SAE. Un INSERT repetido duplica el producto. De ahí que reclamar sea un paso
# aparte (marca EN_CURSO y nadie más la toma), que el resultado se guarde por
# empresa, y que no exista ningún endpoint para «volver a intentar».
#
# Y la cola es SÓLO del tenant dueño de SAE (`require_duenio_de_sae`, 26-sep-2026).
# Pedir, reclamar y reportar: el escritor nunca atiende a otro tenant, así que
# su alta se quedaba 24 h esperando y caducaba como ERROR, y el reporte le
# dejaba estampar una clave que SAE nunca confirmó. Mejor un 403 que lo diga.
# Leer su propia lista (GET /alta-sae) sí puede.

# Las cuatro empresas de SAE. Vacío en la petición = las cuatro: el estado que
# hoy duele es justo el producto que quedó creado en una sola.
_EMPRESAS_SAE = ("02", "03", "04", "05")

# El permiso angosto de la conexión: pedir el alta y crear el producto que la
# acompaña. Ver PERMISOS_CONEXION en core/rbac.py para por qué no es
# `producto:gestionar`.
_ALTA = "producto:alta_sae"

# Una alta reclamada que nadie reporta: el conector corre cada pocos minutos y
# una alta tarda segundos. A la hora se cierra como ERROR para que se vea, pero
# NO se re-encola: si el INSERT alcanzó a entrar, encolarla otra vez duplica.
_ALTA_EN_CURSO_MAX = timedelta(hours=1)
_ALTA_PENDIENTE_MAX = timedelta(hours=24)


def _puede_pedir_alta(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """Quien administra el catálogo, o quien SOLO puede pedir altas.

    La conexión del bot entra por la segunda puerta: `producto:alta_sae` crea el
    producto nuevo que acompaña a la solicitud y la encola, y nada más. No trae
    `producto:gestionar` a propósito — reapuntar un alias afecta a todo el
    catálogo, y eso no se le presta a un mensaje de WhatsApp."""
    if ctx.is_owner or _WRITE in ctx.permissions or _ALTA in ctx.permissions:
        return ctx
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Falta permiso: {_WRITE} o {_ALTA}")


def _expirar_altas_muertas(db: Session, tenant_id) -> None:
    """Cierra como ERROR las altas que ya nadie va a reportar, con el motivo
    dicho: «se reclamó y nadie reportó» NO significa que no se haya creado en
    SAE, y quien la lea tiene que saberlo antes de volver a pedirla."""
    ahora = datetime.now(timezone.utc)
    muertas = (db.query(SolicitudAltaSae)
               .filter(SolicitudAltaSae.tenant_id == tenant_id,
                       or_(and_(SolicitudAltaSae.estado == "PENDIENTE",
                                SolicitudAltaSae.solicitada_at < ahora - _ALTA_PENDIENTE_MAX),
                           and_(SolicitudAltaSae.estado == "EN_CURSO",
                                func.coalesce(SolicitudAltaSae.iniciada_at,
                                              SolicitudAltaSae.solicitada_at)
                                < ahora - _ALTA_EN_CURSO_MAX)))
               .all())
    for m in muertas:
        reclamada = m.estado == "EN_CURSO"
        m.estado = "ERROR"
        m.terminada_at = ahora
        m.motivo = ("El conector la tomó y no reportó: REVISA EN SAE si la clave "
                    "quedó creada antes de volver a pedirla."
                    if reclamada else
                    "Nadie la recogió en 24 h: el conector no corrió.")
    if muertas:
        db.flush()


# ── El núcleo de un alta: lo comparten «dar de alta» suelto (POST /alta-sae, el
# bot) y las claves nuevas que viajan con el producto (2-oct-2026). Una sola
# copia de cada regla: la idempotencia por clave, las empresas válidas y qué
# cuenta como «ya existe en SAE».

def _empresas_de_la_alta(empresas) -> list[str]:
    """Las empresas pedidas, validadas. Vacío = las cuatro."""
    pedidas = [e.strip() for e in (empresas or []) if e.strip()] or list(_EMPRESAS_SAE)
    fuera = [e for e in pedidas if e not in _EMPRESAS_SAE]
    if fuera:
        raise HTTPException(status_code=422,
                            detail=f"Empresa desconocida: {', '.join(fuera)}")
    return pedidas


def _alta_viva(db: Session, tenant_id, clave: str) -> Optional[SolicitudAltaSae]:
    """La alta de esa clave que todavía nadie cerró. Dos altas vivas insertarían
    dos veces el mismo artículo: quien pide otra, recibe ésta."""
    return (db.query(SolicitudAltaSae)
            .filter(SolicitudAltaSae.tenant_id == tenant_id,
                    SolicitudAltaSae.tipo == "ALTA",
                    func.upper(func.btrim(SolicitudAltaSae.clave)) == clave,
                    SolicitudAltaSae.estado.in_(("PENDIENTE", "EN_CURSO")))
            .order_by(SolicitudAltaSae.solicitada_at.asc()).first())


def _empresas_donde_existe(db: Session, tenant_id, clave: str, empresas) -> list[str]:
    """De las empresas pedidas, en cuáles el espejo ya tiene la clave ACTIVA.

    Sólo cuentan las empresas que se piden (30-sep-2026): una clave creada en
    02 y que falta en 03 se completa pidiendo SÓLO la 03. Y sólo el SAE 10,
    que es donde se escribe: el catálogo del SAE 9 (91/92/94) vive en el mismo
    tenant desde el 26-sep-2026 y una clave que sólo existe allá no hace a esta
    alta repetida."""
    return sorted({e for (e,) in (db.query(ClaveSae.empresa)
                                  .filter(ClaveSae.tenant_id == tenant_id,
                                          ClaveSae.empresa.in_(empresas),
                                          func.upper(func.btrim(ClaveSae.clave)) == clave,
                                          ClaveSae.activa.is_(True))
                                  .all())})


def _datos_alta(*, descripcion, unidad, linea, esquema, sat, sat_unidad, nota=None) -> dict:
    """Lo que el escritor necesita para el INSERT, guardado completo en la
    solicitud: si el catálogo cambia mañana, el alta que se aplicó es ESTA."""
    return {
        "descripcion": (descripcion or "").strip()[:60],
        "unidad": (unidad or "PIEZA").strip().upper(),
        "linea": (linea or "").strip().upper() or None,
        "esquema": esquema,
        "sat": (sat or "").strip() or None,
        "sat_unidad": (sat_unidad or "").strip() or None,
        "nota": (nota or "").strip() or None,
    }


def _solicitud_alta(ctx: AuthContext, *, clave: str, producto_id, datos: dict,
                    empresas: list[str], origen: Optional[str]) -> SolicitudAltaSae:
    return SolicitudAltaSae(
        tenant_id=ctx.tenant_id,
        origen=(origen or "UI").strip().upper()[:12],
        producto_id=producto_id,
        clave=clave,
        datos=datos,
        empresas=empresas,
        solicitada_por=ctx.user_id,
    )


def _encolar_alta_sae(db: Session, ctx: AuthContext, *, clave: str, producto_id,
                      datos: dict, empresas: list[str],
                      origen: str = "UI") -> Optional[SolicitudAltaSae]:
    """Encola el alta de una clave que viaja con su producto, o reusa la viva.

    A diferencia de POST /alta-sae, aquí una empresa que ya tiene la clave NO
    es un 409: se quita de la lista, porque el producto ya se está guardando
    con esa clave y en esa empresa basta con ligarla. Si no queda ninguna, no
    se encola nada (None): es una liga, no un alta.

    El INSERT va en un SAVEPOINT: si otra petición encoló la misma clave entre
    la lectura y la escritura, el índice único `uq_alta_sae_viva` lo frena y se
    devuelve la suya, sin tirar el producto que se está guardando.
    """
    ya = set(_empresas_donde_existe(db, ctx.tenant_id, clave, empresas))
    faltan = [e for e in empresas if e not in ya]
    if not faltan:
        return None
    viva = _alta_viva(db, ctx.tenant_id, clave)
    if viva is not None:
        return viva
    sol = _solicitud_alta(ctx, clave=clave, producto_id=producto_id, datos=datos,
                          empresas=faltan, origen=origen)
    try:
        with db.begin_nested():
            db.add(sol)
            db.flush()
    except IntegrityError:
        viva = _alta_viva(db, ctx.tenant_id, clave)
        if viva is None:
            raise HTTPException(status_code=409,
                                detail=f"Ya hay un alta viva para la clave {clave}") from None
        return viva
    db.refresh(sol)
    return sol


def _esquema_sae_del_producto(db: Session, prod: Producto) -> int:
    """El número de esquema de impuestos de SAE (CVE_ESQIMPU) del producto: en
    el tenant dueño de SAE los esquemas se catalogaron con ese número como
    código (1, 2, 4, 5, 7, 8). Sale del PRODUCTO y no de quien pide, para que
    el alta no diga una cosa y el producto otra."""
    esq = None
    if prod.esquema_impuesto_id is not None:
        esq = (db.query(EsquemaImpuesto)
               .filter(EsquemaImpuesto.id == prod.esquema_impuesto_id,
                       EsquemaImpuesto.deleted_at.is_(None))
               .one_or_none())
    if esq is None:
        raise HTTPException(status_code=422,
                            detail="El producto no tiene esquema de impuesto; elígelo antes de pedir el alta en SAE")
    codigo = (esq.codigo or "").strip()
    if not codigo.isdigit() or not 1 <= int(codigo) <= 99:
        raise HTTPException(
            status_code=422,
            detail=(f"El esquema {codigo or esq.nombre} no tiene número de SAE: el alta "
                    "necesita el esquema como lo numera SAE (1, 2, 4…)"),
        )
    return int(codigo)


def _encolar_altas_del_producto(db: Session, ctx: AuthContext, prod: Producto,
                                altas: Optional[list[AltaSaeProductoIn]]) -> list[SolicitudAltaSae]:
    """«Crear clave nueva en SAE» desde el editor de producto (2-oct-2026).

    Cada clave tiene que ser una de las del producto que se acaba de guardar
    (la base o la de una presentación): así el producto ya la lleva mientras
    el alta está pendiente y nunca existe sin clave. Si SAE la rechaza, la
    pantalla lo ve como «Alta con error» en el estado de la clave.

    La clave nueva tiene que poderse escribir tal cual en SAE (letras, números
    y guion, hasta 16). Las viejas con punto se pueden LIGAR, no crear.
    """
    if not altas:
        return []
    from ...services import sae_escritura

    _expirar_altas_muertas(db, ctx.tenant_id)
    del_producto = {norm_clave(prod.clave_sae)} if (prod.clave_sae or "").strip() else set()
    del_producto |= set(claves_sae_por_presentacion(prod.presentaciones).values())
    esquema = _esquema_sae_del_producto(db, prod)
    salida: list[SolicitudAltaSae] = []
    for a in altas:
        clave = sae_escritura.normalizar_clave(a.clave)
        if not clave:
            raise HTTPException(
                status_code=422,
                detail=(f"La clave {a.clave.strip()!r} no se puede crear en SAE: sólo letras "
                        "sin acento, números y guion, hasta 16"),
            )
        if clave not in del_producto:
            raise HTTPException(
                status_code=422,
                detail=(f"La clave {clave} no es de este producto: ponla en la unidad base "
                        "o en una presentación antes de pedir su alta"),
            )
        if not a.descripcion.strip():
            raise HTTPException(status_code=422,
                                detail=f"El alta de {clave} necesita descripción")
        empresas = sorted(set(_empresas_de_la_alta(a.empresas)))
        sol = _encolar_alta_sae(
            db, ctx, clave=clave, producto_id=prod.id, empresas=empresas,
            datos=_datos_alta(descripcion=a.descripcion, unidad=a.unidad, linea=a.linea,
                              esquema=esquema, sat=prod.clave_sat, sat_unidad=a.sat_unidad),
        )
        if sol is not None and all(s.id != sol.id for s in salida):
            salida.append(sol)
    return salida


@router.post("/alta-sae", response_model=AltaSaeOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_duenio_de_sae)])
def pedir_alta_sae(
    payload: AltaSaeIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(_puede_pedir_alta),
):
    """Pide crear un producto en SAE, en las empresas que se indiquen.

    Idempotente por clave, y eso es el candado, no una comodidad: dos «dale de
    alta AJOKG» seguidos por WhatsApp tienen que devolver LA MISMA solicitud,
    porque dos altas vivas insertarían dos veces el mismo artículo.

    Si la clave ya está en el catálogo espejo de SAE (`claves_sae`, activa), no
    se encola nada y se contesta 409: el producto ya existe allá y lo que hace
    falta es ligarlo, no crearlo.
    """
    _expirar_altas_muertas(db, ctx.tenant_id)
    clave = (payload.clave or "").strip().upper()
    if not clave:
        raise HTTPException(status_code=422, detail="La clave no puede ir vacía")
    empresas = _empresas_de_la_alta(payload.empresas)

    viva = _alta_viva(db, ctx.tenant_id, clave)
    if viva is not None:
        return viva

    # Pedirla también donde ya existe es 409 (ver `_empresas_donde_existe`).
    ya = _empresas_donde_existe(db, ctx.tenant_id, clave, empresas)
    if ya:
        raise HTTPException(
            status_code=409,
            detail=(f"La clave {clave} ya existe en SAE (empresa {', '.join(ya)}); "
                    "no hay que crearla, hay que ligarla al producto."),
        )

    producto_id = payload.producto_id
    if producto_id is not None:
        ensure_fk(db, Producto, producto_id, "producto_id")
    else:
        # El producto del catálogo nace AQUÍ, no en quien pide: así la conexión
        # del bot puede dar de alta sin que se le preste `producto:gestionar`.
        # Si ya hay uno con el mismo nombre exacto, se reusa — dos productos
        # iguales son justo el problema que el catálogo existe para evitar. El
        # parecido NO cuenta: ligar por difuso apuntaría el alta al producto
        # equivocado.
        nombre = payload.descripcion.strip()
        existente = (db.query(Producto)
                     .filter(Producto.tenant_id == ctx.tenant_id,
                             Producto.deleted_at.is_(None),
                             func.upper(func.btrim(Producto.nombre)) == nombre.upper())
                     .first())
        if existente is not None:
            producto_id = existente.id
        elif payload.crear_producto:
            base = (payload.unidad or "KILO").strip().upper()
            base = "KILO" if base in ("KG", "KILO", "KILOGRAMO") else base or "PIEZA"
            # EL ESQUEMA DE IMPUESTO SÍ VIAJA (22-sep-2026). Nacía sin él, y un
            # producto sin esquema contesta 0% de IVA en /productos/impuestos
            # —por el respaldo a producto.iva_tasa, que también nace en 0—,
            # así que el alta fabricaba en silencio un producto exento. El bot
            # ya manda el número de esquema de SAE y aquí se cruza por código,
            # que es como están catalogados de este lado (1, 2, 4, 5, 7, 8).
            esq_id = None
            if payload.esquema is not None:
                esq = (db.query(EsquemaImpuesto)
                       .filter(EsquemaImpuesto.tenant_id == ctx.tenant_id,
                               EsquemaImpuesto.deleted_at.is_(None),
                               func.btrim(EsquemaImpuesto.codigo) == str(payload.esquema).strip())
                       .first())
                esq_id = esq.id if esq else None
            prod = Producto(
                tenant_id=ctx.tenant_id,
                sku=_next_sku(db),
                nombre=nombre[:254],
                clave_sat=(payload.sat or "01010101").strip()[:8],
                unidad_sat=(payload.sat_unidad or "H87").strip()[:3],
                clave_sae=clave[:50],
                unidad_base=base[:20],
                presentaciones={base: 1},
                presentacion_default=base[:20],
                categoria_id=categoria_sin_categorizar(db, ctx.tenant_id).id,
                esquema_impuesto_id=esq_id,
                created_by=ctx.user_id,
            )
            db.add(prod)
            flush_or_conflict(db, detail=f"No pude crear el producto {nombre}")
            producto_id = prod.id

    sol = _solicitud_alta(
        ctx, clave=clave, producto_id=producto_id, empresas=empresas, origen=payload.origen,
        datos=_datos_alta(descripcion=payload.descripcion, unidad=payload.unidad,
                          linea=payload.linea, esquema=payload.esquema, sat=payload.sat,
                          sat_unidad=payload.sat_unidad, nota=payload.nota),
    )
    db.add(sol)
    flush_or_conflict(db, detail="Ya hay un alta viva para esa clave")
    db.refresh(sol)
    return sol


@router.post("/cambio-sae", response_model=AltaSaeOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_duenio_de_sae)])
def pedir_cambio_sae(
    payload: CambioSaeIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(_puede_pedir_alta),
):
    """Pide cambiar un artículo que YA existe en SAE: descripción, línea,
    unidad, esquema de impuestos, clave SAT, o reactivarlo.

    Entra a la misma cola que las altas y con sus mismas reglas. Lo escribe el
    Facturador (`sae_escritura`); el bot sólo lo pide. Dar de baja y cambiar
    precio NO están aquí a propósito: la baja nunca es por iniciativa propia
    (regla del dueño) y los precios quedaron fuera del alcance autorizado.

    Una sola viva por clave, pero un cambio que todavía nadie tomó ABSORBE al
    nuevo: «cámbiale la línea» y luego «y la unidad» son un solo UPDATE, no dos
    solicitudes peleando por la misma clave. Si ya se está escribiendo, se
    contesta 409: mezclarlo a medio camino no se sabe si alcanzó a entrar.
    """
    from ...services import sae_escritura

    _expirar_altas_muertas(db, ctx.tenant_id)
    clave = sae_escritura.normalizar_clave(payload.clave)
    if not clave:
        raise HTTPException(status_code=422,
                            detail=f"La clave {payload.clave!r} no es una clave de SAE válida")
    empresas = [e.strip() for e in (payload.empresas or []) if e.strip()] or list(_EMPRESAS_SAE)
    fuera = [e for e in empresas if e not in _EMPRESAS_SAE]
    if fuera:
        raise HTTPException(status_code=422,
                            detail=f"Empresa desconocida: {', '.join(fuera)}")
    crudos = payload.model_dump(include=set(sae_escritura.CAMPOS_CAMBIO), exclude_none=True)
    if not crudos.get("reactivar"):
        crudos.pop("reactivar", None)
    try:
        cambios = sae_escritura.validar_cambios(crudos)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    viva = (db.query(SolicitudAltaSae)
            .filter(SolicitudAltaSae.tenant_id == ctx.tenant_id,
                    SolicitudAltaSae.tipo == "CAMBIO",
                    SolicitudAltaSae.clave == clave,
                    SolicitudAltaSae.estado.in_(("PENDIENTE", "EN_CURSO")))
            .with_for_update().first())
    if viva is not None:
        if viva.estado == "EN_CURSO":
            raise HTTPException(
                status_code=409,
                detail=f"Ya se está escribiendo un cambio a {clave} en SAE; pide este cuando termine.")
        viva.datos = {**(viva.datos or {}), **cambios}
        viva.empresas = sorted(set(viva.empresas or []) | set(empresas))
        db.flush()
        db.refresh(viva)
        return viva

    sol = SolicitudAltaSae(
        tenant_id=ctx.tenant_id,
        tipo="CAMBIO",
        origen=(payload.origen or "UI").strip().upper()[:12],
        producto_id=payload.producto_id,
        clave=clave,
        datos=cambios,
        empresas=empresas,
        solicitada_por=ctx.user_id,
    )
    db.add(sol)
    flush_or_conflict(db, detail="Ya hay un cambio vivo para esa clave")
    db.refresh(sol)
    return sol


@router.get("/claves-sae", response_model=list[ClaveSaeBuscadaOut])
def buscar_claves_sae(
    clave: Optional[str] = Query(default=None, max_length=50,
                                 description="Clave exacta; se compara en MAYÚSCULAS y sin espacios"),
    q: Optional[str] = Query(default=None, max_length=80,
                             description="Texto que se busca en la clave y en la descripción"),
    empresa: Optional[str] = Query(default=None, pattern=r"^\d{2}$",
                                   description="Sólo esa empresa de SAE (02, 03, 04, 05)"),
    solo_activas: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """¿Existe esta clave en SAE, en qué empresas, y qué producto la lleva?

    Es la pregunta que el bot hace ANTES de pedir un alta o un cambio
    (26-sep-2026): si la clave no existe en ninguna empresa, es un alta; si
    existe, es un cambio, y `empresas` son justo las que hay que mandarle a
    `POST /productos/cambio-sae` — mandarlo a las cuatro sale PARCIAL donde no
    existe.

    Sale del espejo `claves_sae`, no de SAE en vivo: es instantáneo y lo que el
    Facturador acaba de escribir allá ya está reflejado. Agrupa por clave y va
    primero la coincidencia exacta.
    """
    from ...services.claves_sae import buscar_claves, clave_de_busqueda

    if not clave_de_busqueda(clave) and not (q or "").strip():
        raise HTTPException(status_code=422, detail="hace falta `clave` o `q`")
    # Sin empresa, sólo las del SAE 10: con lo que contesta aquí el bot decide
    # entre un alta y un cambio EN EL SAE 10, y una clave del SAE 9 (que nunca
    # se escribe) lo mandaría a cambiar algo que en el 10 no existe.
    return buscar_claves(db, ctx.tenant_id, clave=clave, q=q, empresa=empresa,
                         empresas=None if empresa else _EMPRESAS_SAE,
                         solo_activas=solo_activas, limit=limit)


@router.get("/claves-sae/estado", response_model=list[ClaveSaeEstadoOut])
def estado_claves_sae(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Cada clave de SAE que usa el catálogo: en qué empresas existe (espejo) y
    su última solicitud de ALTA. Es lo que pinta el chip de cada clave en
    Productos —«en SAE», «Alta pendiente», «Alta con error»— sin preguntar
    clave por clave (2-oct-2026).

    Todas las claves de los productos no borrados, la base y las de cada
    presentación. Tres consultas en total, no una por clave. Un tenant sin SAE
    recibe la lista vacía y no un 403: la pantalla es la misma para todos y
    simplemente no tiene chips que pintar.
    """
    if not es_duenio_de_sae(ctx.tenant_id):
        return []
    _expirar_altas_muertas(db, ctx.tenant_id)
    claves: set[str] = set()
    for base, presentaciones in (db.query(Producto.clave_sae, Producto.presentaciones)
                                 .filter(Producto.tenant_id == ctx.tenant_id,
                                         Producto.deleted_at.is_(None))
                                 .all()):
        if (base or "").strip():
            claves.add(norm_clave(base))
        claves.update(claves_sae_por_presentacion(presentaciones).values())
    if not claves:
        return []

    llave = func.upper(func.btrim(ClaveSae.clave))
    espejo: dict[str, dict[str, ClaveSaeEstadoEmpresa]] = {}
    for clave, empresa, activa in (db.query(llave, ClaveSae.empresa, ClaveSae.activa)
                                   .filter(ClaveSae.tenant_id == ctx.tenant_id,
                                           ClaveSae.empresa.in_(_EMPRESAS_SAE),
                                           llave.in_(claves))
                                   .all()):
        espejo.setdefault(clave, {})[empresa] = ClaveSaeEstadoEmpresa(activa=bool(activa))

    # La ÚLTIMA alta de cada clave (DISTINCT ON): una vieja con error que ya se
    # volvió a pedir no debe seguir pintando «Alta con error».
    llave_sol = func.upper(func.btrim(SolicitudAltaSae.clave))
    ultimas = {
        norm_clave(sol.clave): sol
        for sol in (db.query(SolicitudAltaSae)
                    .filter(SolicitudAltaSae.tenant_id == ctx.tenant_id,
                            SolicitudAltaSae.tipo == "ALTA",
                            llave_sol.in_(claves))
                    .distinct(llave_sol)
                    .order_by(llave_sol, SolicitudAltaSae.solicitada_at.desc())
                    .all())
    }
    return [
        ClaveSaeEstadoOut(
            clave=c,
            empresas=dict(sorted(espejo.get(c, {}).items())),
            solicitud=(SolicitudSaeResumenOut.model_validate(ultimas[c])
                       if c in ultimas else None),
        )
        for c in sorted(claves)
    ]


@router.get("/claves-cliente", response_model=list[ClaveClienteOut])
def claves_de_cliente(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Los SKU exclusivos de cliente: códigos del catálogo de cliente que NO son
    la clave del producto en esa unidad (ZANA-FRUT-508 de Balles y Jubran sobre
    la ZANAHORIA de todos, 2-oct-2026). Productos los pinta junto a la clave
    para que se vea que es el mismo producto con el artículo de ese cliente y
    nadie dé de alta otro.

    La unidad es la de la fila (`presentacion`) o, vacía, la base del producto:
    la misma que usa el export (`codigo_cliente_de`)."""
    filas = (db.query(ProductoCliente.producto_id, ProductoCliente.presentacion,
                      ProductoCliente.codigo_cliente, Cliente.legal_name,
                      Producto.unidad_base, Producto.clave_sae, Producto.presentaciones)
             .join(Producto, Producto.id == ProductoCliente.producto_id)
             .join(Cliente, Cliente.id == ProductoCliente.cliente_id)
             .filter(ProductoCliente.tenant_id == ctx.tenant_id,
                     ProductoCliente.codigo_cliente.isnot(None),
                     Producto.deleted_at.is_(None))
             .all())
    juntas: dict[tuple, set[str]] = {}
    for pid, pres, codigo, cliente, base, clave_base, presentaciones in filas:
        codigo = norm_clave(codigo)
        unidad = (pres or base or "").strip().upper()
        if not codigo or not unidad:
            continue
        propia = (norm_clave(clave_base) if unidad == (base or "").upper()
                  else claves_sae_por_presentacion(presentaciones).get(unidad) or norm_clave(clave_base))
        if codigo == propia:
            continue
        juntas.setdefault((pid, unidad, codigo), set()).add(cliente)
    return [ClaveClienteOut(producto_id=pid, unidad=u, clave=c, clientes=sorted(cs))
            for (pid, u, c), cs in sorted(juntas.items(), key=lambda kv: (str(kv[0][0]), kv[0][1], kv[0][2]))]


# Las descripciones de los esquemas de SAE (IMPU) cambian casi nunca y «Así
# está en SAE» se abre en cada liga: quince minutos en memoria, por empresa,
# igual que /sae/catalogos. Lo que falla no se guarda.
_ESQUEMAS_SAE_TTL_SEG = 15 * 60
_esquemas_sae_cache: dict[str, tuple[float, dict[int, str]]] = {}
_esquemas_sae_lock = threading.Lock()


def _esquemas_sae(empresa: str) -> dict[int, str]:
    """{número de esquema: descripción} de IMPU<empresa>. Vacío si SAE no
    contesta: la descripción es informativa y no vale tumbar la respuesta."""
    from ...services import sae_lectura

    ahora = time.monotonic()
    with _esquemas_sae_lock:
        guardado = _esquemas_sae_cache.get(empresa)
    if guardado and ahora - guardado[0] < _ESQUEMAS_SAE_TTL_SEG:
        return guardado[1]
    try:
        mapa = {e["codigo"]: e["descripcion"] for e in sae_lectura.esquemas_de(empresa)}
    except Exception as e:  # noqa: BLE001 — informativo: sin él la respuesta sigue
        logger.warning("esquemas de SAE %s: %s: %s", empresa, type(e).__name__, e)
        return {}
    with _esquemas_sae_lock:
        _esquemas_sae_cache[empresa] = (ahora, mapa)
    return mapa


@router.get("/claves-sae/{clave}/en-sae", response_model=ArticuloSaeOut,
            dependencies=[Depends(require_duenio_de_sae)])
def articulo_en_sae(
    clave: str,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """«Así está en SAE»: el artículo leído EN VIVO de INVE en cada empresa
    donde existe, para compararlo con lo capturado en el producto antes de
    ligar la clave (2-oct-2026). Descripción, unidad, línea, esquema y claves
    SAT, con la descripción oficial de cada clave SAT (informativa).

    Se leen las empresas 02-05 donde el espejo tiene la clave; si no la tiene
    en ninguna, la 02 (el espejo pudo no haberse puesto al día). Las demás
    salen con `existe=false`.

    NUNCA es un 5xx: con SAE sin configurar o sin contestar, 200 con
    `disponible=false` y el motivo, y las empresas como las dice el espejo. La
    pantalla de ligar se degrada; no se cae por una pregunta informativa.
    """
    from ...services import sae_escritura, sae_lectura

    # Trim + mayúsculas, como el espejo y el lector (norm_clave): una clave
    # vieja con un espacio ADENTRO existe así en INVE, y quitárselo la
    # volvería «no está en SAE». Limpiar más es sólo para claves nuevas.
    c = norm_clave(clave)
    if not c:
        raise HTTPException(status_code=422, detail="La clave no puede ir vacía")
    if len(c) > 50:
        raise HTTPException(status_code=422, detail="La clave de SAE es de hasta 50 caracteres")

    # {empresa: (activa, descripción)} según el espejo
    espejo = {e: (activa, desc) for e, activa, desc in
              (db.query(ClaveSae.empresa, ClaveSae.activa, ClaveSae.descripcion)
               .filter(ClaveSae.tenant_id == ctx.tenant_id,
                       ClaveSae.empresa.in_(_EMPRESAS_SAE),
                       func.upper(func.btrim(ClaveSae.clave)) == c)
               .all())}

    def _segun_espejo(motivo: str) -> ArticuloSaeOut:
        return ArticuloSaeOut(clave=c, disponible=False, motivo=motivo, empresas={
            e: (ArticuloSaeEmpresaOut(existe=True, activa=bool(espejo[e][0]),
                                      descripcion=espejo[e][1])
                if e in espejo else ArticuloSaeEmpresaOut())
            for e in _EMPRESAS_SAE
        })

    if not sae_lectura.disponible():
        return _segun_espejo("el Facturador no tiene configurado el acceso a SAE")
    leidos: dict[str, Optional[dict]] = {}
    for emp in sorted(espejo) or ["02"]:
        try:
            leidos[emp] = sae_lectura.articulo(emp, c)
        except Exception as e:  # noqa: BLE001 — se degrada, nunca 5xx
            if sae_lectura.es_falla_de_red(e):
                logger.warning("así está en SAE %s (%s): %s: %s", c, emp, type(e).__name__, e)
            else:
                # No es la red: la consulta está mal. Se degrada igual, pero
                # que quede en la bitácora, porque es un bug.
                logger.exception("así está en SAE %s (%s): la lectura falló", c, emp)
            return _segun_espejo(f"SAE no contestó (empresa {emp}): {type(e).__name__}: {e}")

    encontrados = [a for a in leidos.values() if a]
    sats = {a["sat"] for a in encontrados if a.get("sat")}
    unidades_sat = {a["sat_unidad"] for a in encontrados if a.get("sat_unidad")}
    desc_sat = dict(db.query(SatClaveProdServ.clave, SatClaveProdServ.descripcion)
                    .filter(SatClaveProdServ.clave.in_(sats)).all()) if sats else {}
    desc_unidad = dict(db.query(SatClaveUnidad.clave, SatClaveUnidad.nombre)
                       .filter(SatClaveUnidad.clave.in_(unidades_sat)).all()) if unidades_sat else {}
    # UNI_MED de SAE → la unidad del Facturador: el mapa del escritor, al revés.
    canonica = {sae_escritura.UNIDADES[u][0]: u for u in sae_escritura.UNIDADES_CANONICAS}

    empresas: dict[str, ArticuloSaeEmpresaOut] = {}
    for emp in _EMPRESAS_SAE:
        a = leidos.get(emp)
        if not a:
            empresas[emp] = ArticuloSaeEmpresaOut()
            continue
        esquema = a.get("esquema")
        empresas[emp] = ArticuloSaeEmpresaOut(
            existe=True,
            activa=a.get("activa"),
            descripcion=a.get("descripcion"),
            unidad=a.get("unidad"),
            unidad_canonica=canonica.get((a.get("unidad") or "").upper()),
            linea=a.get("linea"),
            esquema=esquema,
            esquema_descripcion=(_esquemas_sae(emp).get(esquema) or None
                                 if esquema is not None else None),
            sat=a.get("sat"),
            sat_descripcion=desc_sat.get(a.get("sat") or ""),
            sat_unidad=a.get("sat_unidad"),
            sat_unidad_descripcion=desc_unidad.get(a.get("sat_unidad") or ""),
        )
    return ArticuloSaeOut(clave=c, disponible=True, empresas=empresas)


@router.get("/alta-sae", response_model=Page[AltaSaeOut])
def listar_altas_sae(
    estado: Optional[str] = Query(default=None, max_length=10),
    clave: Optional[str] = Query(default=None, max_length=20),
    # ALTA por omisión: así contesta exactamente como antes de que la cola
    # llevara cambios, que es lo que el acuse del bot espera al preguntar por
    # una clave. TODAS = las dos.
    tipo: str = Query(default="ALTA", pattern="^(ALTA|CAMBIO|TODAS)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Las altas pedidas, lo último primero. Es lo que sondea el bot para su
    acuse y lo que la pantalla de catálogo pinta como «pendiente en SAE»."""
    _expirar_altas_muertas(db, ctx.tenant_id)
    q = db.query(SolicitudAltaSae).filter(SolicitudAltaSae.tenant_id == ctx.tenant_id)
    if tipo != "TODAS":
        q = q.filter(SolicitudAltaSae.tipo == tipo)
    if estado:
        q = q.filter(SolicitudAltaSae.estado == estado.strip().upper())
    if clave:
        q = q.filter(func.upper(func.btrim(SolicitudAltaSae.clave)) == clave.strip().upper())
    q = q.order_by(SolicitudAltaSae.solicitada_at.desc())
    return paginate(q, AltaSaeOut, limit, offset)


def reclamar_siguiente_sae(db: Session, tenant_id) -> Optional[SolicitudAltaSae]:
    """Reclama la siguiente solicitud (alta o cambio) y la marca EN_CURSO.

    `skip_locked`: dos reclamos a la vez no pueden tomar la misma y escribirla
    dos veces en SAE. Reclama UNA a la vez a propósito: si el proceso muere a
    media escritura, hay que poder decir exactamente de qué clave hay que ir a
    ver en SAE. La usan la puerta del bot y el escritor del Facturador.
    """
    _expirar_altas_muertas(db, tenant_id)
    sol = (db.query(SolicitudAltaSae)
           .filter(SolicitudAltaSae.tenant_id == tenant_id,
                   SolicitudAltaSae.estado == "PENDIENTE")
           .order_by(SolicitudAltaSae.solicitada_at.asc())
           .with_for_update(skip_locked=True).first())
    if sol is None:
        return None
    sol.estado = "EN_CURSO"
    sol.iniciada_at = datetime.now(timezone.utc)
    db.flush()
    db.refresh(sol)
    return sol


@router.get("/alta-sae/pendiente", response_model=Optional[AltaSaeOut],
            dependencies=[Depends(require_duenio_de_sae)])
def reclamar_alta_sae(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("factura:espejo")),
):
    """La puerta del conector del BOT: pregunta si hay algo que aplicar.

    UN SOLO ESCRITOR. Con la escritura del Facturador encendida
    (`sae_escritura.activo()`), esta puerta ya no entrega nada: el bot
    contesta «no hay altas pendientes» y el que escribe es el Facturador. Si
    las dos puertas repartieran, dos escritores se tomarían la cola a la vez y
    la garantía de no duplicar dependería de la suerte.

    Y sólo entrega ALTAS: el aplicador del bot no sabe hacer cambios, y
    reclamarle uno sería cerrarlo como hecho sin haberlo escrito.
    """
    from ...services import sae_escritura
    if sae_escritura.activo():
        return None
    _expirar_altas_muertas(db, ctx.tenant_id)
    sol = (db.query(SolicitudAltaSae)
           .filter(SolicitudAltaSae.tenant_id == ctx.tenant_id,
                   SolicitudAltaSae.tipo == "ALTA",
                   SolicitudAltaSae.estado == "PENDIENTE")
           .order_by(SolicitudAltaSae.solicitada_at.asc())
           .with_for_update(skip_locked=True).first())
    if sol is None:
        return None
    sol.estado = "EN_CURSO"
    sol.iniciada_at = datetime.now(timezone.utc)
    db.flush()
    db.refresh(sol)
    return sol


@router.post("/alta-sae/{solicitud_id}/reporte", response_model=AltaSaeOut,
             dependencies=[Depends(require_duenio_de_sae)])
def reportar_alta_sae(
    solicitud_id: UUID,
    payload: AltaSaeReporteIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("factura:espejo")),
):
    """El conector reporta qué creó, por empresa. Esto CIERRA la solicitud: no
    hay reintento, porque lo que ya entró a SAE no se puede volver a insertar.

    El estado sale de lo reportado, no de un `ok` que alguien manda: todas las
    empresas bien = OK, ninguna = ERROR, mezcla = PARCIAL (falta trabajo y algo
    ya se creó: las dos cosas son verdad y una persona tiene que verlo).

    La clave se estampa en el producto SOLO si alguna empresa la confirmó —
    misma regla que con los folios de SAE: el Facturador no se apunta una clave
    que SAE no haya confirmado.
    """
    return cerrar_solicitud_sae(db, ctx.tenant_id, solicitud_id,
                                payload.por_empresa, payload.motivo)


def cerrar_solicitud_sae(db: Session, tenant_id, solicitud_id, por_empresa: Optional[dict],
                         motivo: Optional[str]) -> SolicitudAltaSae:
    """Cierra una solicitud con lo que contestó SAE, por empresa. Es el cierre
    del reporte del bot y el del escritor del Facturador: una sola regla."""
    sol = (db.query(SolicitudAltaSae)
           .filter(SolicitudAltaSae.tenant_id == tenant_id,
                   SolicitudAltaSae.id == solicitud_id)
           .with_for_update().one_or_none())
    if sol is None:
        raise HTTPException(status_code=404, detail="Esa solicitud de alta no existe")
    if sol.estado in ("OK", "PARCIAL", "ERROR"):
        # Ya cerrada: reportar dos veces sería la puerta de atrás al reintento
        raise HTTPException(
            status_code=409,
            detail=f"Esa alta ya está cerrada como {sol.estado}; no se reporta dos veces.",
        )

    por_empresa = {str(k): v for k, v in (por_empresa or {}).items()
                   if isinstance(v, dict)}
    pedidas = [str(e) for e in (sol.empresas or [])]
    creadas = [e for e in pedidas if (por_empresa.get(e) or {}).get("ok")]
    sol.estado = ("OK" if creadas and len(creadas) == len(pedidas)
                  else "PARCIAL" if creadas else "ERROR")
    sol.resultado = por_empresa or None
    sol.motivo = (motivo or "").strip() or None
    sol.terminada_at = datetime.now(timezone.utc)

    # La clave se estampa sólo en un ALTA: un cambio no crea nada que ligar.
    if creadas and sol.producto_id and sol.tipo == "ALTA":
        prod = (db.query(Producto)
                .filter(Producto.tenant_id == tenant_id,
                        Producto.id == sol.producto_id,
                        Producto.deleted_at.is_(None))
                .one_or_none())
        # No se pisa una clave que el producto ya traía: si son distintas, eso
        # es un conflicto que decide una persona, no este reporte. Y la clave de
        # una PRESENTACIÓN (SANDIAPZ) ya vive en su presentación: estamparla
        # como la de la base haría salir el kilo con la clave de la pieza.
        de_presentacion = {
            str(v.get("clave_sae") or "").strip().upper()
            for v in (prod.presentaciones or {}).values() if isinstance(v, dict)
        } if prod is not None else set()
        if (prod is not None and not (prod.clave_sae or "").strip()
                and sol.clave.strip().upper() not in de_presentacion):
            confirmada = next((por_empresa[e].get("clave") for e in creadas
                               if (por_empresa[e].get("clave") or "").strip()), None)
            prod.clave_sae = (confirmada or sol.clave).strip().upper()[:50]
    if creadas:
        _reflejar_en_espejo_de_claves(db, tenant_id, sol, por_empresa, creadas)
    db.flush()
    db.refresh(sol)
    return sol


def _reflejar_en_espejo_de_claves(db: Session, tenant_id, sol: SolicitudAltaSae,
                                  por_empresa: dict, creadas: list[str]) -> None:
    """Lo que SAE confirmó en cada empresa entra YA al espejo `claves_sae`.

    Sin esto, un alta recién escrita no existía para el Facturador hasta la
    siguiente lectura de INVE (horas): el candado 409 de otra alta de la misma
    clave la dejaba pasar, y la búsqueda de claves del bot contestaba «no
    existe» de algo que ya estaba en SAE (26-sep-2026).

    Sólo empresas con `ok`. Lo que viaja es lo que SAE dijo: el escritor del
    Facturador reporta `descripcion` y `activa` leídos de vuelta en SAE ya
    confirmada la escritura. Si el reporte no los trae (el conector del bot, o
    esa lectura de vuelta falló), se usa lo pedido — y en un `ya_existia` sin
    lectura no se afirma nada: la clave existe, pero pudo estar de baja, y el
    espejo no la vuelve activa por eso.

    Un CAMBIO sólo toca el espejo si reactivó o cambió la descripción: línea,
    unidad, esquema y SAT no viven en `claves_sae`.

    Va en la MISMA transacción del cierre, pero en un SAVEPOINT: si el espejo
    fallara, el cierre sigue. Perder el cierre por esto dejaría la solicitud
    EN_CURSO hasta expirar como «revisa en SAE», que es mucho peor que un
    espejo que se pone al día en la siguiente lectura.
    """
    from ...services import claves_sae, sae_escritura

    datos = sol.datos or {}
    if sol.tipo == "CAMBIO" and not (datos.get("reactivar") or datos.get("descripcion")):
        return
    for empresa in creadas:
        r = por_empresa.get(empresa) or {}
        clave = (r.get("clave") or sol.clave or "").strip()
        leido = "activa" in r or "descripcion" in r
        if leido:
            desc, activa = r.get("descripcion"), r.get("activa")
        elif sol.tipo == "CAMBIO":
            desc = datos.get("descripcion")
            activa = True if datos.get("reactivar") else None
        elif r.get("ya_existia"):
            desc, activa = None, None
        else:
            desc = sae_escritura.ascii_mayus(datos.get("descripcion") or "").strip()[:60]
            activa = True
        try:
            with db.begin_nested():
                claves_sae.reflejar_escritura(db, tenant_id, empresa, clave, desc, activa)
        except SQLAlchemyError:
            logger.exception("espejo de claves: no pude reflejar %s %s en la empresa %s",
                             sol.tipo, clave, empresa)


@router.get("/{producto_id}", response_model=ProductoOut)
def get_producto(
    producto_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, Producto, producto_id)


@router.get("/{producto_id}/alias", response_model=list[AliasOut])
def listar_alias(
    producto_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Cómo escriben los clientes este producto.

    Hasta ahora los alias solo se PODÍAN CREAR: se acumulaban por importación,
    IA, el bot y la captura, y no había ninguna pantalla donde verlos. Un alias
    apuntando al producto equivocado dejaba órdenes sin cotizar sin que nada lo
    dijera (pasó con «CALABAZA CRIOLLA TIERNA», que iba a un producto sin
    precio en ninguna lista).

    Se ordena global primero y luego por cliente, que es como se leen: lo
    global aplica a todos y es lo que hay que mirar con más cuidado.
    """
    get_or_404(db, Producto, producto_id)     # 404 y alcance de tenant por RLS
    filas = (
        db.query(ProductoAlias, Cliente.legal_name, Sucursal.nombre)
        .outerjoin(Cliente, Cliente.id == ProductoAlias.cliente_id)
        .outerjoin(Sucursal, Sucursal.id == ProductoAlias.sucursal_id)
        .filter(ProductoAlias.producto_id == producto_id)
        .order_by(ProductoAlias.cliente_id.nullsfirst(), ProductoAlias.alias)
        .all()
    )
    if not filas:
        return []
    # ¿Alguno de estos textos apunta ADEMÁS a otro producto? Una sola consulta
    # para todos, en vez de una por alias.
    normalizados = {a.alias_normalizado for a, _, _ in filas}
    otros: dict[str, list[str]] = {}
    for norm, nombre in (
        db.query(ProductoAlias.alias_normalizado, Producto.nombre)
        .join(Producto, Producto.id == ProductoAlias.producto_id)
        .filter(
            ProductoAlias.alias_normalizado.in_(normalizados),
            ProductoAlias.producto_id != producto_id,
        )
        .distinct()
        .all()
    ):
        otros.setdefault(norm, []).append(nombre)
    return [
        AliasOut(
            id=a.id,
            texto=a.alias,
            origen=a.origen,
            cliente_id=a.cliente_id,
            cliente_nombre=cliente,
            sucursal_id=a.sucursal_id,
            sucursal_nombre=sucursal,
            ambiguo=a.alias_normalizado in otros,
            tambien_en=sorted(otros.get(a.alias_normalizado, [])),
            created_at=a.created_at,
        )
        for a, cliente, sucursal in filas
    ]


@router.patch("/{producto_id}", response_model=ProductoOut)
def update_producto(
    producto_id: UUID,
    payload: ProductoUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    _sin_sae_no_hay_altas(ctx, payload.altas_sae)
    obj = get_or_404(db, Producto, producto_id)
    data = payload.model_dump(exclude_unset=True)
    data.pop("altas_sae", None)            # no es columna: se encola abajo
    if data.get("nombre"):
        data["nombre"] = data["nombre"].strip().upper()   # nombres siempre en mayúsculas
    _clave_sae_limpia(db, ctx, data, obj)
    # Misma regla que en el alta: el SKU interno es numérico del servidor; el
    # código del cliente vive en producto_clientes. Solo se valida el CAMBIO —
    # un producto viejo con SKU alfanumérico se sigue pudiendo editar (la
    # pantalla lo reenvía tal cual al guardar el nombre).
    nuevo_sku = (data.get("sku") or "").strip()
    if nuevo_sku and nuevo_sku != (obj.sku or "") and not nuevo_sku.isdigit():
        raise HTTPException(
            status_code=422,
            detail=(
                "El SKU interno es numérico y lo genera el servidor; los códigos "
                "del cliente se registran en el catálogo del cliente, no como SKU"
            ),
        )
    if "categoria_id" in data:
        # Igual que el esquema: se puede CAMBIAR, nunca vaciar (2-oct-2026).
        if data["categoria_id"] is None:
            raise HTTPException(status_code=422, detail="Elige la categoría del producto")
        ensure_fk(db, CategoriaProducto, data["categoria_id"], "categoria_id")
    if "esquema_impuesto_id" in data:
        # Quitárselo es la otra forma de dejar un producto sin IVA. Se puede
        # CAMBIAR, nunca vaciar.
        if data["esquema_impuesto_id"] is None:
            raise HTTPException(
                status_code=422,
                detail="Elige el esquema de impuesto: sin él el producto no lleva IVA y su factura saldría mal",
            )
        ensure_fk(db, EsquemaImpuesto, data["esquema_impuesto_id"], "esquema_impuesto_id")
    # Los huecos que el producto YA traía (2-oct-2026): siguen naciendo por
    # rutas sin candado (nueva presentación desde la remisión, importación), y
    # el aviso de Remisiones los llena de uno en uno con este PATCH. Uno
    # inactivo no tolera nada: reactivarlo exige todas sus claves.
    huecos_antes = (_unidades_sin_clave_sae(obj.unidad_base, obj.clave_sae, obj.presentaciones)
                    if obj.activo else [])
    for key, value in data.items():
        setattr(obj, key, value)
    # La regla de las claves se mide sobre el producto que QUEDA, y sólo si se
    # tocó lo que las define: un producto viejo con huecos se sigue pudiendo
    # renombrar y se le pueden ir llenando; lo que no se puede es quitarle una
    # clave ni sumarle una unidad sin la suya.
    if obj.activo and any(k in data for k in ("clave_sae", "presentaciones",
                                              "unidad_base", "activo")):
        _exigir_claves_sae(ctx, obj.unidad_base, obj.clave_sae, obj.presentaciones,
                           toleradas=huecos_antes)
    obj.updated_by = ctx.user_id
    flush_or_conflict(db, detail=_DUP)
    altas = _encolar_altas_del_producto(db, ctx, obj, payload.altas_sae)
    db.refresh(obj)
    obj.altas_sae = altas
    return obj


@router.put("/{producto_id}/clave-sae", response_model=ClaveSaeLineaOut)
def guardar_clave_sae_linea(
    producto_id: UUID,
    payload: ClaveSaeLineaIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Corrige la clave de SAE desde una línea de remisión, en el MISMO nivel
    del que sale hoy — así la línea cambia de verdad y no se pisa otra capa:

    1. La presentación ya tiene clave propia, o no es la unidad base (SANDIA
       en PIEZA): se guarda en ESA presentación.
    2. La unidad base con clave del catálogo del cliente: se corrige esa fila
       (la de su plaza si existe, si no la genérica). Sólo el código; el nombre
       con el que el cliente conoce el producto no se toca.
    3. Si no, la clave base del producto.

    Vacía quita la clave de ese nivel y la línea cae al siguiente.
    """
    prod = get_or_404(db, Producto, producto_id)
    clave = (payload.clave or "").strip().upper()[:50] or None
    pres = (payload.presentacion or "").strip().upper()
    base = (prod.unidad_base or "").strip().upper()
    mapa = dict(prod.presentaciones or {})
    llave_pres = next((k for k in mapa if str(k).strip().upper() == pres), None) if pres else None

    raw = mapa.get(llave_pres) if llave_pres is not None else None
    tiene_propia = isinstance(raw, dict) and str(raw.get("clave_sae") or "").strip()
    if llave_pres is not None and (tiene_propia or pres != base):
        entrada = dict(raw) if isinstance(raw, dict) else {"factor": raw if raw is not None else 1}
        if clave:
            entrada["clave_sae"] = clave
        else:
            entrada.pop("clave_sae", None)
        # Dict NUEVO: mutar el del ORM no marca el JSONB sucio.
        prod.presentaciones = {**mapa, llave_pres: entrada}
        prod.updated_by = ctx.user_id
        db.flush()
        return ClaveSaeLineaOut(clave=clave, origen="presentacion", presentacion=pres)

    if payload.cliente_id is not None:
        filas = (
            db.query(ProductoCliente)
            .filter(ProductoCliente.cliente_id == payload.cliente_id,
                    ProductoCliente.producto_id == producto_id,
                    ProductoCliente.codigo_cliente.isnot(None))
            .all()
        )
        fila = next((f for f in filas if payload.sucursal_id is not None
                     and f.sucursal_id == payload.sucursal_id), None) or next(
            (f for f in filas if f.sucursal_id is None), None)
        if fila is not None:
            fila.updated_by = ctx.user_id
            if clave:
                fila.codigo_cliente = clave
            elif (fila.nombre_cliente or "").strip():
                fila.codigo_cliente = None
            else:
                db.delete(fila)
            db.flush()
            return ClaveSaeLineaOut(clave=clave, origen="cliente", presentacion=pres or None)

    prod.clave_sae = clave
    prod.updated_by = ctx.user_id
    db.flush()
    return ClaveSaeLineaOut(clave=clave, origen="producto", presentacion=pres or None)


@router.post("/{producto_id}/presentaciones", response_model=ProductoOut)
def agregar_presentacion(
    producto_id: UUID,
    payload: PresentacionCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Agrega UNA presentación al producto, sin pisar las que ya tiene.

    Existe para que las pantallas que no son Productos (la remisión, la lista de
    precios) puedan dar de alta CAJA sobre la marcha sin leer-modificar-escribir
    el diccionario entero, que es como dos capturistas a la vez se borran la
    presentación el uno al otro.
    """
    obj = get_or_404(db, Producto, producto_id)
    nombre = payload.nombre.strip().upper()
    if not nombre:
        raise HTTPException(status_code=422, detail="La presentación necesita nombre")
    conocidas = {(obj.unidad_base or "").upper()} | {
        str(k).upper() for k in (obj.presentaciones or {})
    }
    if nombre in conocidas:
        raise HTTPException(
            status_code=422,
            detail=f"{obj.nombre} ya maneja {nombre}; edítala en Productos si el factor cambió",
        )
    sat = (payload.unidad_sat or "").strip().upper() or _UNIDAD_A_SAT.get(nombre) or obj.unidad_sat
    # Asignar un dict NUEVO: mutar el que trae el ORM no marca el JSONB sucio y
    # el cambio se pierde sin error.
    obj.presentaciones = {
        **(obj.presentaciones or {}),
        nombre: {"factor": float(payload.factor), "sat": sat},
    }
    obj.updated_by = ctx.user_id
    db.flush()
    db.refresh(obj)
    return obj


@router.delete("/{producto_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_producto(
    producto_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("producto:eliminar")),
):
    obj = get_or_404(db, Producto, producto_id)
    obj.deleted_at = func.now()
    obj.updated_by = ctx.user_id   # quién lo dio de baja, no solo cuándo
    db.flush()
    return None
