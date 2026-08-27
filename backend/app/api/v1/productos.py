"""Productos — CRUD.

Reads gated by `menu:productos` (so a TOMADOR can look products up while taking
an order); writes by `producto:gestionar`. The optional `categoria_id` and
`esquema_impuesto_id` FKs are re-validated under the tenant scope before they
are persisted (RLS does not constrain Postgres FK checks).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.ratelimit import enforce
from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import (
    CategoriaProducto,
    Cliente,
    EsquemaImpuesto,
    ListaPrecios,
    Precio,
    Producto,
    ProductoAlias,
    ProductoCliente,
    SatClaveProdServ,
    SatClaveUnidad,
)
from ...schemas.producto import (
    AliasIn,
    CandidatoOut,
    ImportErrorFila,
    ImportFilaPreview,
    ImportIn,
    ImportPreviewOut,
    ImportResultOut,
    LineaPegadaOut,
    MatchIn,
    MatchResultOut,
    ParsePegadoIn,
    ProductoCreate,
    ProductoOut,
    ProductoUpdate,
    SugerenciaSatOut,
    SugerirSatBatchIn,
)
from ...schemas.common import Page
from ...services.categoria_codigo import generate_unique_codigo
from ...services.importar_productos import (
    ImportProductosError,
    extraer_con_ia,
    generar_plantilla,
    normalizar_unidad,
    parsear_plantilla,
)
from ...services.sat_catalogo import sugerir_batch
from ...services.producto_match import (
    alias_del_tenant,
    aprender_alias,
    buscar,
    normalizar,
    parsear_pegado,
    productos_activos,
    sugerir_con_ia,
)
from ._helpers import ensure_fk, flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/productos", tags=["productos"])

_READ = "menu:productos"
_WRITE = "producto:gestionar"
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


@router.get("", response_model=Page[ProductoOut])
def list_productos(
    q: Optional[str] = Query(default=None, max_length=254),
    categoria_id: Optional[UUID] = Query(default=None),
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
    if activo is not None:
        query = query.filter(Producto.activo.is_(activo))
    query = query.order_by(Producto.nombre.asc())
    return paginate(query, ProductoOut, limit, offset)


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


@router.post("/match", response_model=list[MatchResultOut])
def match_productos(
    payload: MatchIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Cruza textos libres (tecleados/pegados) contra el catálogo: exacto → alias
    aprendido → difuso, y opcionalmente IA para los que no resuelvan."""
    if payload.usar_ia:
        # La rama IA manda el catálogo completo como contexto (cuesta dinero).
        enforce(f"producto-ia:{ctx.tenant_id}", 120, 3600)
    catalogo = productos_activos(db)   # una sola carga para todos los textos
    aliases = alias_del_tenant(db)     # idem: sin esto era un SELECT por texto
    resultados: list[dict] = []
    sin_match: list[str] = []
    for texto in payload.textos:
        cands = buscar(db, ctx.tenant_id, texto, limit=payload.limit, prods=catalogo, aliases=aliases)
        resultados.append({"texto": texto, "candidatos": [
            CandidatoOut(
                producto_id=c.producto_id, sku=c.sku, nombre=c.nombre, score=c.score, origen=c.origen,
                presentaciones=c.presentaciones, presentacion_default=c.presentacion_default,
                unidad_base=c.unidad_base,
            )
            for c in cands
        ]})
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
                r["candidatos"] = [CandidatoOut(
                    producto_id=p.id, sku=p.sku, nombre=p.nombre, score=85, origen="ia",
                    presentaciones=p.presentaciones or {}, presentacion_default=p.presentacion_default,
                    unidad_base=p.unidad_base,
                )]
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

    catalogo = productos_activos(db)   # una sola carga para todas las filas
    aliases = alias_del_tenant(db)     # idem: sin esto era un SELECT por fila
    resultados: list[dict] = []
    sin_match: list[str] = []
    for f in filas:
        # Varios candidatos para poblar el desplegable Match IA (el front muestra ≥80%).
        cands = buscar(db, ctx.tenant_id, f["producto"], limit=8, prods=catalogo, aliases=aliases)
        resultados.append({
            "texto": f["producto"],
            "cantidad": f["cantidad"],
            "precio": f["precio"],
            "presentacion": f["presentacion"],
            "candidatos": [
                CandidatoOut(
                    producto_id=c.producto_id, sku=c.sku, nombre=c.nombre, score=c.score, origen=c.origen,
                    presentaciones=c.presentaciones, presentacion_default=c.presentacion_default,
                    unidad_base=c.unidad_base,
                )
                for c in cands
            ],
        })
        if not cands:
            sin_match.append(f["producto"])

    # IA solo para los que ni exacto/alias/difuso resolvieron (sinónimos regionales).
    if payload.usar_ia and sin_match:
        ia = sugerir_con_ia(db, ctx.tenant_id, sin_match)
        pids = {pid for pid in ia.values() if pid}
        prods = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(pids)).all()} if pids else {}
        for r in resultados:
            pid = ia.get(r["texto"])
            if not r["candidatos"] and pid and pid in prods:
                p = prods[pid]
                r["candidatos"] = [CandidatoOut(
                    producto_id=p.id, sku=p.sku, nombre=p.nombre, score=85, origen="ia",
                    presentaciones=p.presentaciones or {}, presentacion_default=p.presentacion_default,
                    unidad_base=p.unidad_base,
                )]
    return resultados


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
    envenenaría el cruce de todo el negocio."""
    ensure_fk(db, Producto, payload.producto_id, "producto_id")
    existente = (
        db.query(ProductoAlias)
        .filter(ProductoAlias.alias_normalizado == normalizar(payload.texto))
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
    aprender_alias(db, ctx.tenant_id, payload.texto, payload.producto_id, origen="MANUAL", user_id=ctx.user_id)
    return {"ok": True}


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


def _cruce_confiable(nombre_archivo: str, nombre_candidato: str) -> bool:
    """¿Se puede auto-sugerir VINCULAR un candidato difuso?

    El scorer de búsqueda (token_set_ratio) ignora tokens sobrantes: tecleando
    "ajo" debe aparecer "AJO EN POLVO". Pero al IMPORTAR esa dirección liga mal.
    Dos reglas:
    1. Si el nombre del ARCHIVO trae tokens con contenido que el candidato no
       tiene (es MÁS específico: "AJO EN POLVO" vs "AJO"), no se auto-vincula.
    2. Si el CANDIDATO trae una transformación que el archivo no pide
       ("CHILE JALAPEÑO" vs "...PICADOS 215 GR LATA"), tampoco.
    En ambos casos la fila queda como "crear" con los candidatos visibles para
    que el usuario decida en un clic."""
    qa = {t for t in normalizar(nombre_archivo).split() if t not in _STOP_CRUCE}
    pa = {t for t in normalizar(nombre_candidato).split() if t not in _STOP_CRUCE}
    if qa - pa:                      # tokens extra del lado del archivo
        return False
    return not ((pa - qa) & _PROCESADO)   # transformación solo en el candidato


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

    try:
        filas = parsear_plantilla(data, filename) if ext in _TABULARES else None
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

    # Cruce contra el catálogo, una sola carga para todas las filas.
    catalogo = productos_activos(db)
    aliases = alias_del_tenant(db)     # una sola carga: sin esto, un SELECT por fila
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

    # Categorías y esquemas del tenant, por nombre/código normalizado.
    cats_tenant = {
        normalizar(c.nombre) for c in
        db.query(CategoriaProducto).filter(CategoriaProducto.deleted_at.is_(None)).all()
    }
    esquemas_tenant = set()
    for e in db.query(EsquemaImpuesto).filter(EsquemaImpuesto.deleted_at.is_(None)).all():
        esquemas_tenant.add(normalizar(e.codigo or ""))
        esquemas_tenant.add(normalizar(e.nombre or ""))

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
        cands = buscar(db, ctx.tenant_id, f["nombre"], limit=5, prods=catalogo, aliases=aliases)
        if sugerido is None and cands and cands[0].score >= 80:
            top = cands[0]
            if top.origen in ("exacto", "alias") or _cruce_confiable(f["nombre"], top.nombre):
                sugerido = top.producto_id
        if sugerido is not None and not ya_vinculado:
            ya_vinculado = sugerido in productos_vinculados

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
            esquema=f.get("esquema") or "",
            baja=(f.get("estatus") or "") in ("BAJA", "INACTIVO", "B"),
            clave_sat_valida=(clave_f in claves_ok) if clave_f else None,
            unidad_sat_valida=(unidad_sat_f in unidades_ok) if unidad_sat_f else None,
            nueva_presentacion=nueva_presentacion,
            producto_id=sugerido,
            candidatos=[
                CandidatoOut(
                    producto_id=c.producto_id, sku=c.sku, nombre=c.nombre,
                    score=c.score, origen=c.origen,
                    presentaciones=c.presentaciones,
                    presentacion_default=c.presentacion_default,
                    unidad_base=c.unidad_base,
                )
                for c in cands
            ],
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
    cats_nuevas = sorted({
        f.categoria for f in activas
        if f.categoria and normalizar(f.categoria) not in cats_tenant
    })
    esq_no_encontrados = sorted({
        f.esquema for f in activas
        if f.esquema and normalizar(f.esquema) not in esquemas_tenant
    })
    return ImportPreviewOut(
        formato=formato,
        filas=out,
        faltan_clave_sat=sum(1 for f in activas if not f.clave_sat),
        faltan_unidad_sat=sum(1 for f in activas if not f.unidad_sat),
        categorias_nuevas=cats_nuevas,
        esquemas_no_encontrados=esq_no_encontrados,
        filas_sin_esquema=sum(1 for f in activas if not f.esquema),
        tiene_precios=any(f.precio for f in activas),
    )


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
        # Un solo cliente CON lista → la suya (comportamiento de siempre).
        if lista_id is None and len(clientes) == 1 and clientes[0].lista_precios_id:
            lista_id = clientes[0].lista_precios_id
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
                if cli.lista_precios_id is None:
                    cli.lista_precios_id = lista.id
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

    # SKUs secuenciales sin re-consultar el máximo en cada fila.
    siguiente_sku = _max_sku_num(db)

    creados = vinculados = alias_guardados = precios_guardados = omitidos = 0
    categorias_creadas = presentaciones_agregadas = 0
    errores: list[ImportErrorFila] = []

    for n, fila in enumerate(payload.filas, start=1):
        if fila.accion == "omitir":
            omitidos += 1
            continue
        try:
            with db.begin_nested():   # savepoint: una fila mala no tira el lote
                unidad_fila = normalizar_unidad(fila.unidad_base or "")

                # Categoría: id explícito → nombre del archivo (existente o
                # creada si el lote lo pidió) → sin categoría.
                categoria_id = fila.categoria_id
                if categoria_id is None and (fila.categoria or "").strip():
                    cat = cats_por_nombre.get(normalizar(fila.categoria))
                    if cat is None and payload.crear_categorias:
                        cat = CategoriaProducto(
                            tenant_id=ctx.tenant_id,
                            codigo=generate_unique_codigo(db, ctx.tenant_id, fila.categoria),
                            nombre=fila.categoria.strip(),
                        )
                        db.add(cat)
                        db.flush()
                        cats_por_nombre[normalizar(cat.nombre)] = cat
                        categorias_creadas += 1
                    if cat is not None:
                        categoria_id = cat.id

                # Esquema: id explícito → código/nombre del archivo → default del lote.
                esquema_id = fila.esquema_impuesto_id
                if esquema_id is None and (fila.esquema or "").strip():
                    esq = esquemas_por_clave.get(normalizar(fila.esquema))
                    if esq is not None:
                        esquema_id = esq.id
                if esquema_id is None:
                    esquema_id = payload.esquema_default_id

                if fila.accion == "vincular":
                    if fila.producto_id is None:
                        raise ValueError("Falta el producto a vincular")
                    prod = (
                        db.query(Producto)
                        .filter(Producto.id == fila.producto_id, Producto.deleted_at.is_(None))
                        .one_or_none()
                    )
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
                    _validate_fks(
                        db,
                        categoria_id=categoria_id,
                        esquema_impuesto_id=esquema_id,
                    )
                    unidad_base = unidad_fila or "KILO"
                    sku = (fila.sku or "").strip()
                    if not sku:
                        siguiente_sku += 1
                        sku = f"{siguiente_sku:08d}"
                    prod = Producto(
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
                    )
                    db.add(prod)
                    db.flush()
                    creados += 1

                # Catálogo del cliente: su código → NoIdentificacion, su nombre
                # → Descripcion del CFDI, su presentación → cómo le vende. Se
                # guarda para CADA cliente elegido (una lista, varios clientes).
                codigo_c = (fila.codigo_cliente or "").strip() or None
                nombre_c = (fila.nombre_cliente or "").strip() or None
                if clientes and (codigo_c or nombre_c):
                    for cliente in clientes:
                        pc = (
                            db.query(ProductoCliente)
                            .filter(
                                ProductoCliente.cliente_id == cliente.id,
                                ProductoCliente.producto_id == prod.id,
                            )
                            .one_or_none()
                        )
                        if pc is None:
                            pc = ProductoCliente(
                                tenant_id=ctx.tenant_id,
                                cliente_id=cliente.id,
                                producto_id=prod.id,
                            )
                            db.add(pc)
                        pc.codigo_cliente = codigo_c
                        pc.nombre_cliente = nombre_c
                        if unidad_fila:
                            pc.presentacion = unidad_fila
                        db.flush()
                        alias_guardados += 1
                    # El cruce también aprende el nombre (una vez, es del tenant).
                    if nombre_c:
                        aprender_alias(
                            db, ctx.tenant_id, nombre_c, prod.id,
                            origen="IMPORT", user_id=ctx.user_id,
                        )

                # Precio → lista indicada, EN la presentación de la fila (el
                # precio del MANOJO no es el del KILO).
                if lista_id is not None and fila.precio is not None:
                    presentacion = unidad_fila or prod.presentacion_default or prod.unidad_base or "KILO"
                    precio_row = (
                        db.query(Precio)
                        .filter(
                            Precio.lista_id == lista_id,
                            Precio.producto_id == prod.id,
                            Precio.presentacion == presentacion,
                            Precio.cantidad_minima == 1,
                        )
                        .one_or_none()
                    )
                    if precio_row is None:
                        db.add(Precio(
                            tenant_id=ctx.tenant_id, lista_id=lista_id,
                            producto_id=prod.id, presentacion=presentacion,
                            precio_unitario=fila.precio, cantidad_minima=1,
                        ))
                    else:
                        precio_row.precio_unitario = fila.precio
                    db.flush()
                    precios_guardados += 1
        except (IntegrityError, ValueError) as exc:
            detalle = str(exc)
            if isinstance(exc, IntegrityError):
                detalle = _DUP if "uq_producto_tenant_sku" in str(exc.orig) else "Registro duplicado"
            errores.append(ImportErrorFila(fila=n, error=detalle))

    return ImportResultOut(
        creados=creados, vinculados=vinculados, alias_guardados=alias_guardados,
        precios_guardados=precios_guardados, omitidos=omitidos,
        categorias_creadas=categorias_creadas,
        presentaciones_agregadas=presentaciones_agregadas,
        lista_id=lista_id, lista_nombre=lista_nombre_out,
        errores=errores,
    )


@router.post("", response_model=ProductoOut, status_code=status.HTTP_201_CREATED)
def create_producto(
    payload: ProductoCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    _validate_fks(
        db,
        categoria_id=payload.categoria_id,
        esquema_impuesto_id=payload.esquema_impuesto_id,
    )
    data = payload.model_dump()
    if data.get("nombre"):
        data["nombre"] = data["nombre"].strip().upper()   # nombres siempre en mayúsculas
    if not (data.get("sku") or "").strip():
        data["sku"] = _next_sku(db)   # auto-generate when blank
    obj = Producto(**data, tenant_id=ctx.tenant_id)
    db.add(obj)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.get("/{producto_id}", response_model=ProductoOut)
def get_producto(
    producto_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, Producto, producto_id)


@router.patch("/{producto_id}", response_model=ProductoOut)
def update_producto(
    producto_id: UUID,
    payload: ProductoUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Producto, producto_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("nombre"):
        data["nombre"] = data["nombre"].strip().upper()   # nombres siempre en mayúsculas
    if "categoria_id" in data:
        ensure_fk(db, CategoriaProducto, data["categoria_id"], "categoria_id")
    if "esquema_impuesto_id" in data:
        ensure_fk(db, EsquemaImpuesto, data["esquema_impuesto_id"], "esquema_impuesto_id")
    for key, value in data.items():
        setattr(obj, key, value)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.delete("/{producto_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_producto(
    producto_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Producto, producto_id)
    obj.deleted_at = func.now()
    db.flush()
    return None
