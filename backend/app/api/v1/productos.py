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
)
from ...schemas.common import Page
from ...services.importar_productos import (
    ImportProductosError,
    extraer_con_ia,
    generar_plantilla,
    normalizar_unidad,
    parsear_plantilla,
)
from ...services.producto_match import (
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
    resultados: list[dict] = []
    sin_match: list[str] = []
    for texto in payload.textos:
        cands = buscar(db, ctx.tenant_id, texto, limit=payload.limit, prods=catalogo)
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
    resultados: list[dict] = []
    sin_match: list[str] = []
    for f in filas:
        # Varios candidatos para poblar el desplegable Match IA (el front muestra ≥80%).
        cands = buscar(db, ctx.tenant_id, f["producto"], limit=8, prods=catalogo)
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


def _cruce_confiable(nombre_archivo: str, nombre_candidato: str) -> bool:
    """¿Se puede auto-sugerir VINCULAR un candidato difuso?

    El scorer de búsqueda (token_set_ratio) ignora tokens sobrantes: tecleando
    "ajo" debe aparecer "AJO EN POLVO". Pero al IMPORTAR esa dirección liga mal:
    "AJO EN POLVO" del archivo NO es el "AJO" del catálogo. Regla direccional:
    si el nombre del archivo trae tokens con contenido que el candidato no
    tiene (es MÁS específico), no se auto-vincula — se deja como "crear" con
    los candidatos visibles para que el usuario decida en un clic."""
    qa = {t for t in normalizar(nombre_archivo).split() if t not in _STOP_CRUCE}
    pa = {t for t in normalizar(nombre_candidato).split() if t not in _STOP_CRUCE}
    return not (qa - pa)   # sin tokens extra del lado del archivo


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
    por_sku = {p.sku.strip().upper(): p for p in catalogo if p.sku}

    # Lo que el cliente ya tiene vinculado (por producto y por su código).
    pc_por_producto: dict = {}
    pc_por_codigo: dict = {}
    if cliente_id is not None:
        ensure_fk(db, Cliente, cliente_id, "cliente_id")
        for pc in db.query(ProductoCliente).filter(ProductoCliente.cliente_id == cliente_id).all():
            pc_por_producto[pc.producto_id] = pc
            cod = (pc.codigo_cliente or "").strip().upper()
            if cod:
                pc_por_codigo.setdefault(cod, pc)

    out: list[ImportFilaPreview] = []
    vistos: dict[str, int] = {}   # nombre/código normalizado → primera fila
    for n, f in enumerate(filas, start=1):
        codigo = (f.get("codigo") or "").strip()
        sugerido = None
        ya_vinculado = False

        # Duplicados DENTRO del archivo (listas reales repiten renglones): se
        # marca la repetición para que la UI la omita por default. Mismo nombre
        # con OTRA unidad no es duplicado (KG vs PZ = dos presentaciones).
        claves = [f"n:{normalizar(f['nombre'])}|{f.get('unidad') or ''}"] + (
            [f"c:{codigo.upper()}"] if codigo else [])
        duplicada_de = next((vistos[k] for k in claves if k in vistos), None)
        for k in claves:
            vistos.setdefault(k, n)

        # 1) El código del cliente ya está vinculado → ese producto, sin dudar.
        pc = pc_por_codigo.get(codigo.upper()) if codigo else None
        if pc is not None:
            sugerido = pc.producto_id
            ya_vinculado = True

        # 2) El código coincide EXACTO con un SKU interno.
        if sugerido is None and codigo and codigo.upper() in por_sku:
            sugerido = por_sku[codigo.upper()].id

        # 3) Cruce por nombre (exacto → alias → difuso). Los difusos solo se
        #    auto-sugieren si el cruce es confiable en la dirección de importar.
        cands = buscar(db, ctx.tenant_id, f["nombre"], limit=5, prods=catalogo)
        if sugerido is None and cands and cands[0].score >= 80:
            top = cands[0]
            if top.origen in ("exacto", "alias") or _cruce_confiable(f["nombre"], top.nombre):
                sugerido = top.producto_id
        if sugerido is not None and not ya_vinculado:
            ya_vinculado = sugerido in pc_por_producto

        out.append(ImportFilaPreview(
            fila=n,
            nombre=f["nombre"],
            codigo=codigo,
            descripcion=f.get("descripcion") or "",
            unidad=f.get("unidad") or "",
            precio=f.get("precio") or "",
            clave_sat=f.get("clave_sat") or "",
            unidad_sat=f.get("unidad_sat") or "",
            codigo_barras=f.get("codigo_barras") or "",
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
        ))
    return ImportPreviewOut(formato=formato, filas=out)


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
    cliente = None
    if payload.cliente_id is not None:
        cliente = get_or_404(db, Cliente, payload.cliente_id)

    lista_id = None
    if payload.guardar_precios:
        lista_id = payload.lista_id or (cliente.lista_precios_id if cliente else None)
        if lista_id is None:
            raise HTTPException(
                status_code=422,
                detail="Para guardar precios se necesita una lista de precios "
                       "(el cliente no tiene lista asignada)",
            )
        ensure_fk(db, ListaPrecios, lista_id, "lista_id")

    # SKUs secuenciales sin re-consultar el máximo en cada fila.
    siguiente_sku = _max_sku_num(db)

    creados = vinculados = alias_guardados = precios_guardados = omitidos = 0
    errores: list[ImportErrorFila] = []

    for n, fila in enumerate(payload.filas, start=1):
        if fila.accion == "omitir":
            omitidos += 1
            continue
        try:
            with db.begin_nested():   # savepoint: una fila mala no tira el lote
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
                    vinculados += 1
                else:  # crear
                    _validate_fks(
                        db,
                        categoria_id=fila.categoria_id,
                        esquema_impuesto_id=fila.esquema_impuesto_id,
                    )
                    unidad_base = normalizar_unidad(fila.unidad_base or "") or "KILO"
                    sku = (fila.sku or "").strip()
                    if not sku:
                        siguiente_sku += 1
                        sku = f"{siguiente_sku:08d}"
                    prod = Producto(
                        tenant_id=ctx.tenant_id,
                        sku=sku,
                        nombre=fila.nombre.strip().upper(),
                        descripcion=(fila.descripcion or "").strip() or None,
                        categoria_id=fila.categoria_id,
                        esquema_impuesto_id=fila.esquema_impuesto_id,
                        clave_sat=(fila.clave_sat or "").strip() or "01010101",
                        unidad_sat=(fila.unidad_sat or "").strip().upper()
                                   or _UNIDAD_A_SAT.get(unidad_base, "KGM"),
                        unidad_base=unidad_base,
                        presentaciones={unidad_base: 1},
                        presentacion_default=unidad_base,
                        codigo_barras=(fila.codigo_barras or "").strip() or None,
                    )
                    db.add(prod)
                    db.flush()
                    creados += 1

                # Catálogo del cliente: su código → NoIdentificacion, su nombre
                # → Descripcion del CFDI. Solo si algo viene capturado.
                if cliente is not None:
                    codigo_c = (fila.codigo_cliente or "").strip() or None
                    nombre_c = (fila.nombre_cliente or "").strip() or None
                    if codigo_c or nombre_c:
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
                        db.flush()
                        alias_guardados += 1
                        # El cruce también aprende el nombre del cliente.
                        if nombre_c:
                            aprender_alias(
                                db, ctx.tenant_id, nombre_c, prod.id,
                                origen="IMPORT", user_id=ctx.user_id,
                            )

                # Precio → lista de precios del cliente (o la indicada).
                if lista_id is not None and fila.precio is not None:
                    presentacion = prod.presentacion_default or prod.unidad_base or "KILO"
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
        precios_guardados=precios_guardados, omitidos=omitidos, errores=errores,
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
