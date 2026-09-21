"""Remisiones — CRUD + confirmar/cancelar con efecto en inventario (Phase 4e).

Reads gated by `menu:remisiones`; writes by `remision:gestionar`.

Lifecycle: BORRADOR (editable, no stock effect) → RESERVADO (trae folio de
factura de SAE: mercancía comprometida con un comprobante de fuera; tampoco
mueve inventario y se edita igual) → CONFIRMADA (salida directa:
disponible baja, one SALIDA_REMISION movement per line; la línea guarda
lote_id/cantidad_surtida para poder restituir) → CANCELADA (restituye:
disponible sube, CANCELACION_REMISION). A draft cancels with no inventory
effect. La salida usa el lote default de (producto, almacén) — lot-selection/
FIFO is a later refinement. `cantidad_reservada` ya no se usa (decisión
2026-07-29: confirmar = el camión salió; la columna queda en 0).
"""
from __future__ import annotations

import html as html_mod
import re
import unicodedata

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from rapidfuzz import fuzz
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field as PydField
from sqlalchemy import BigInteger, func, or_
from sqlalchemy.orm import Session, joinedload

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import (
    Almacen,
    CategoriaProducto,
    ClaveSae,
    Cliente,
    Devolucion,
    EsquemaImpuesto,
    Factura,
    LineaDevolucion,
    LineaRemision,
    ListaPrecios,
    LoteInventario,
    OCRecibida,
    Producto,
    ProductoCliente,
    Proyecto,
    Remision,
    Sucursal,
    Tenant,
)
from ...services import email as email_service
from ...services.espejo_cruce import ligar_remision_con_espejo
from ...services.fiscal import calcular_linea_producto
from ...services.importar_remisiones import (
    ImportError_,
    agrupar_por_folio,
    normalizar_folio,
    normalizar_nombre,
    parsear_excel,
)
from ...services.producto_match import (
    alias_del_tenant,
    aprender_alias,
    aprender_alias_con_alcance,
    buscar,
    normalizar,
    normalizar_catalogo,
    productos_activos,
)
from ...services.precios import resolver_precios_lote
from ...services.series import consumir_folio, resolver_serie, siguiente_folio
from ...services.sucursales import es_sucursal_de
from ...schemas.common import Page
from ...schemas.remision import (
    CancelarRemisionIn,
    ClaveSaeEnUso,
    ClaveSaeSugerida,
    ClavesSaeOut,
    ConfirmarRemisionIn,
    CruzarLineaIn,
    LiberarPedidoIn,
    RemisionCreate,
    RemisionDetailOut,
    RemisionOut,
    RemisionUpdate,
)
from ...services.inventario import build_movimiento, lotes_for_update, presentacion_factor, resolve_lote
from ...services.remision_pdf import build_remision_pdf, build_remisiones_pdf
from ._helpers import ensure_fk, flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/remisiones", tags=["remisiones"])

_READ = "menu:remisiones"
_WRITE = "remision:gestionar"
_ZERO = Decimal("0")
_DUP = "Folio de remisión duplicado"


def _norm_codigo(v) -> str:
    """Clave del cliente comparable: sin espacios, sin acentos y en mayúsculas.

    El master escribe "AJO -FRUT-017" donde el catálogo guarda "AJO-FRUT-017", y
    la ñ va y viene entre los dos ("PIÑA-FRUT-350" = "PINA-FRUT-350"). El cruce
    sigue siendo exacto, solo que no castiga el espaciado ni la acentuación.
    """
    s = unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", "", s).upper()


def _ensure_productos(db: Session, producto_ids) -> None:
    """`ensure_fk(Producto)` para N líneas en UNA consulta.

    Uno por línea eran N viajes a una BD remota (una remisión de 50 partidas
    los pagaba todos antes de empezar a guardar). Se valida el conjunto de una
    y basta con que falte uno: mismo 422 y mismo texto que daba `ensure_fk`.

    No es un lujo de rendimiento: `ensure_fk` existe por RLS —Postgres no
    aplica RLS al chequeo de una FK, así que un id de otro tenant pasaría el
    constraint— y esta consulta corre en la misma sesión con el tenant puesto.
    """
    ids = {pid for pid in producto_ids if pid is not None}
    if not ids:
        return
    vistos = {
        row[0]
        for row in db.query(Producto.id)
        .filter(Producto.id.in_(ids), Producto.deleted_at.is_(None))
        .all()
    }
    faltan = ids - vistos
    if faltan:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="producto_id inválido o fuera de alcance",
        )


def _precios_de_lineas(db: Session, rem: Remision, lineas: list[dict]) -> list:
    """Precio de cada línea, resolviendo en UN lote las que no lo traen.

    El frontend omite `precio_unitario` en toda línea cuyo precio no se tecleó
    a mano, así que una captura normal las manda casi todas sin precio: una
    cascada de ~6-14 consultas POR LÍNEA contra la BD remota (50 partidas ≈ 25 s
    de guardado). `resolver_precios_lote` da lo mismo en ~6 consultas totales.

    Sin precio resuelto la línea entra en $0 a propósito: capturar sin precio
    es legítimo mientras es borrador y el candado vive en `exigir_precios`.
    """
    faltantes = [i for i, ln in enumerate(lineas) if ln.get("precio_unitario") is None]
    resueltos: dict[int, Decimal] = {}
    if faltantes:
        res = resolver_precios_lote(
            db,
            items=[
                {
                    "producto_id": lineas[i]["producto_id"],
                    "presentacion": lineas[i]["presentacion"],
                    "cantidad": lineas[i]["cantidad_solicitada"],
                }
                for i in faltantes
            ],
            cliente_id=rem.cliente_facturacion_id,
            sucursal_id=rem.sucursal_id,
            serie_id=rem.serie_id,
            proyecto_id=rem.proyecto_id,
            lista_id=rem.lista_precios_id,
        )
        for i, r in zip(faltantes, res, strict=True):
            resueltos[i] = r["precio"] if r and r.get("precio") is not None else _ZERO
    return [
        ln["precio_unitario"] if ln.get("precio_unitario") is not None else resueltos[i]
        for i, ln in enumerate(lineas)
    ]


def _fiscal_por_producto(db: Session, producto_ids) -> dict:
    """{producto_id: (Producto, EsquemaImpuesto|None)} para el cálculo fiscal
    de líneas — una sola carga por request."""
    ids = set(producto_ids)
    productos = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(ids)).all()}
    esq_ids = {p.esquema_impuesto_id for p in productos.values() if p.esquema_impuesto_id}
    esquemas = (
        {e.id: e for e in db.query(EsquemaImpuesto).filter(EsquemaImpuesto.id.in_(esq_ids)).all()}
        if esq_ids else {}
    )
    return {
        pid: (p, esquemas.get(p.esquema_impuesto_id) if p.esquema_impuesto_id else None)
        for pid, p in productos.items()
    }


def _next_folio(db: Session, tenant_id, *, sucursal_id=None, cliente_id=None, serie_id=None) -> str:
    """Folio `{codigo}{N}` (serie y número juntos, sin guion) de la serie de remisión
    resuelta (override → sucursal → cliente → default), contador sin huecos. Si no hay
    serie aplicable, cae a la serie 'R' por código y, en último caso, a max+1 (back-compat)."""
    serie = resolver_serie(
        db, tenant_id, "REMISION", serie_id=serie_id, sucursal_id=sucursal_id, cliente_id=cliente_id
    )
    if serie is not None:
        folio = consumir_folio(db, serie.id)
        if folio is not None:
            return f"{serie.codigo}{folio}"
    folio = siguiente_folio(db, tenant_id, codigo="R", tipo_documento="REMISION")
    if folio is not None:
        return f"R{folio}"
    mx = 0
    for (f,) in db.query(Remision.folio_interno).filter(Remision.folio_interno.isnot(None)).all():
        if not f or not f.startswith("R"):
            continue
        num = f[1:].lstrip("-")  # tolera "R5" y "R-5" (legado)
        if num.isdigit():
            mx = max(mx, int(num))
    return f"R{mx + 1}"


# Orden natural del folio: como texto, "RZEHMOHOS9" > "RZEHMOHOS72" y el listado
# sale barajado (9, 8, 72, 7, 6, 37…). Se separa el prefijo de serie del número
# final para comparar el número como número (los folios los pone el sistema sin
# ceros a la izquierda, así que el texto solo no ordena).
_FOLIO_PREFIJO = func.regexp_replace(Remision.folio_interno, r"\d+$", "")
_FOLIO_NUMERO = func.cast(func.substring(Remision.folio_interno, r"(\d+)$"), BigInteger)


def _adjuntar_sin_clave(db: Session, tenant_id, rems: list) -> None:
    """Cuelga de cada remisión cuántas partidas vivas NO tienen clave SAE del
    cliente — el preflight del candado del export: que el revisor lo vea al
    revisar, no hasta que el lote ya está armado en el modal de exportar.
    Cuenta con el MISMO helper que la validación (services/export_sae), así los
    números siempre casan. Una remisión ya amparada por SAE no se marca."""
    from ...services.export_sae import lineas_clave_no_facturable, lineas_sin_clave

    pendientes = [r for r in rems if not r.factura_sae and r.estado != "CANCELADA"]
    faltan = lineas_sin_clave(db, tenant_id, pendientes)
    # Y el otro candado del masivo: la clave está, pero la empresa de SAE de esa
    # plaza no la factura. Sin esto la remisión se ve limpia y muere al exportar.
    no_facturables = lineas_clave_no_facturable(db, tenant_id, pendientes)
    for r in rems:
        r.sin_clave_sae = len(faltan.get(r.id, [])) or None
        r.clave_no_en_sae = len(no_facturables.get(r.id, {})) or None


def _adjuntar_oc(db: Session, rems: list) -> None:
    """Cuelga de cada remisión su OC original, en DOS consultas para toda la
    página: la que la generó (`remision_id`) y, si no la hay, la que traiga el
    mismo folio del cliente (las importadas del master no pasaron por la
    bandeja, pero comparten "su pedido")."""
    if not rems:
        return
    ids = [r.id for r in rems]
    folios = {r.su_pedido for r in rems if r.su_pedido}
    por_remision: dict = {}
    for oc in db.query(OCRecibida).filter(OCRecibida.remision_id.in_(ids)).all():
        por_remision.setdefault(oc.remision_id, oc)
    por_folio: dict = {}
    if folios:
        for oc in db.query(OCRecibida).filter(OCRecibida.folio_externo.in_(folios)).all():
            por_folio.setdefault(normalizar_folio(oc.folio_externo), oc)
    for r in rems:
        oc = por_remision.get(r.id) or (
            por_folio.get(normalizar_folio(r.su_pedido)) if r.su_pedido else None
        )
        if oc is None:
            continue
        r.oc_id = oc.id
        r.oc_archivo_url = oc.archivo_url
        r.oc_archivo_nombre = oc.archivo_nombre
        r.oc_cambio_abierto = oc.cambio_abierto
        r.oc_cambio_resumen = oc.cambio_resumen


@router.get("", response_model=Page[RemisionOut])
def list_remisiones(
    estado: Optional[str] = Query(default=None, max_length=20),
    cliente_id: Optional[UUID] = Query(default=None),
    # Filtro por plaza (ticket 86bby31f9): «todas las remisiones de Pachuca»
    # es una consulta diaria con clientes multi-plaza.
    sucursal_id: Optional[UUID] = Query(default=None),
    fecha_desde: Optional[date] = Query(default=None),
    fecha_hasta: Optional[date] = Query(default=None),
    # Solo las que llegaron sin revisar: es la bandeja de trabajo del revisor.
    revision_pendiente: Optional[bool] = Query(default=None),
    # Buscar POR COMO LA NOMBRA QUIEN PREGUNTA: el folio interno, el pedido/OC
    # del cliente ("HO-34") o el folio de factura de SAE. Va en el servidor
    # porque el buscador de la tabla solo ve la página cargada y las remisiones
    # viejas se le escapan.
    q: Optional[str] = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = (
        db.query(Remision)
        .options(joinedload(Remision.factura))
        .filter(Remision.deleted_at.is_(None))
    )
    if ctx.cliente_scope:
        # Candado del portal: solo documentos de SUS clientes.
        query = query.filter(Remision.cliente_facturacion_id.in_(ctx.cliente_scope))
    if estado:
        query = query.filter(Remision.estado == estado)
    if revision_pendiente is not None:
        query = query.filter(Remision.revision_pendiente.is_(revision_pendiente))
    if cliente_id is not None:
        query = query.filter(Remision.cliente_facturacion_id == cliente_id)
    if sucursal_id is not None:
        query = query.filter(Remision.sucursal_id == sucursal_id)
    if fecha_desde:
        query = query.filter(Remision.fecha_remision >= fecha_desde)
    if fecha_hasta:
        query = query.filter(Remision.fecha_remision <= fecha_hasta)
    if q and q.strip():
        # Se busca por PALABRAS, no por la frase exacta: la OC se captura
        # "VH-39SAL-LUN" pero quien pregunta teclea "VH-39 LUN". Cada palabra
        # debe aparecer en alguno de los campos (Y entre palabras, O entre
        # campos); con la frase completa esa búsqueda no devolvía nada.
        for termino in q.split():
            variantes = {termino}
            # SAE muestra los folios rellenos de ceros ("ZHGO 0000588") pero aquí
            # se guardan sin ellos (regla del proyecto): se busca también la
            # variante con los ceros a la izquierda de cada número quitados.
            variantes.add(re.sub(r"(?<!\d)0+(?=\d)", "", termino))
            condiciones = []
            for v in variantes:
                like = f"%{v}%"
                condiciones += [
                    Remision.folio_interno.ilike(like),
                    Remision.su_pedido.ilike(like),
                    Remision.factura_sae.ilike(like),
                    # "ZHGO 588" y "ZHGO588" son la misma factura para quien pregunta.
                    func.replace(Remision.factura_sae, " ", "").ilike(like),
                ]
            query = query.filter(or_(*condiciones))
    query = query.order_by(
        Remision.fecha_remision.desc(),
        _FOLIO_PREFIJO.desc(),
        _FOLIO_NUMERO.desc().nullslast(),
        Remision.folio_interno.desc(),
    )
    def _preparar(rows: list) -> None:
        _adjuntar_oc(db, rows)
        _adjuntar_sin_clave(db, ctx.tenant_id, rows)

    return paginate(query, RemisionOut, limit, offset, preparar=_preparar)


@router.post("", response_model=RemisionDetailOut, status_code=status.HTTP_201_CREATED)
def create_remision(
    payload: RemisionCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ensure_fk(db, Cliente, payload.cliente_facturacion_id, "cliente_facturacion_id")
    ensure_fk(db, Almacen, payload.almacen_id, "almacen_id")
    ensure_fk(db, ListaPrecios, payload.lista_precios_id, "lista_precios_id")
    ensure_fk(db, Proyecto, payload.proyecto_id, "proyecto_id")
    if payload.sucursal_id is not None:
        get_or_404(db, Sucursal, payload.sucursal_id)
        if not es_sucursal_de(db, payload.sucursal_id, payload.cliente_facturacion_id):
            raise HTTPException(status_code=422, detail="El cliente de la remisión no se surte de esa sucursal")
    _ensure_productos(db, [ln.producto_id for ln in payload.lineas])

    # La serie se resuelve ANTES de precificar, no al foliar: además del folio,
    # decide qué lista de precios aplica (el negocio pacta por serie). Se
    # resuelve una vez y se guarda; `_next_folio` recibe ya la decidida, así
    # nadie puede foliar con una serie distinta de la que fijó los precios.
    serie = resolver_serie(
        db, ctx.tenant_id, "REMISION",
        serie_id=payload.serie_id,
        sucursal_id=payload.sucursal_id,
        cliente_id=payload.cliente_facturacion_id,
    )

    rem = Remision(
        tenant_id=ctx.tenant_id,
        folio_interno=_next_folio(
            db, ctx.tenant_id,
            sucursal_id=payload.sucursal_id,
            cliente_id=payload.cliente_facturacion_id,
            serie_id=serie.id if serie is not None else payload.serie_id,
        ),
        cliente_facturacion_id=payload.cliente_facturacion_id,
        almacen_id=payload.almacen_id,
        sucursal_id=payload.sucursal_id,
        lista_precios_id=payload.lista_precios_id,
        proyecto_id=payload.proyecto_id,
        serie_id=serie.id if serie is not None else None,
        fecha_remision=payload.fecha_remision or date.today(),
        fecha_entrega=payload.fecha_entrega,
        canal=payload.canal,
        descuento=payload.descuento,
        notas=payload.notas,
        nota_entrega=payload.nota_entrega,
        factura_sae=(payload.factura_sae or "").strip() or None,
        su_pedido=(payload.su_pedido or "").strip() or None,
        # Traer folio de factura de SAE = mercancía ya comprometida con un
        # comprobante de fuera: nace RESERVADA, no como borrador cualquiera.
        estado="RESERVADO" if (payload.factura_sae or "").strip() else "BORRADOR",
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    db.add(rem)
    db.flush()

    fiscal = _fiscal_por_producto(db, [ln.producto_id for ln in payload.lineas])
    # Precio: manual si se envía; si no, se resuelve por cliente/sucursal/volumen
    # —todas las que falten, en UN lote—. Sin precio no se detiene la captura: la
    # línea entra en 0 y se ve. El candado está más adelante: una remisión con
    # líneas en 0 se guarda como borrador pero no se confirma ni se factura.
    precios = _precios_de_lineas(
        db, rem,
        [
            {
                "producto_id": ln.producto_id,
                "presentacion": ln.presentacion,
                "cantidad_solicitada": ln.cantidad_solicitada,
                "precio_unitario": ln.precio_unitario,
            }
            for ln in payload.lineas
        ],
    )
    subtotal = _ZERO
    iva_total = _ZERO
    ieps_total = _ZERO
    for i, (ln, precio) in enumerate(zip(payload.lineas, precios, strict=True), start=1):
        importe = ln.cantidad_solicitada * precio
        subtotal += importe
        prod, esq = fiscal.get(ln.producto_id, (None, None))
        calc = calcular_linea_producto(prod, esq, importe, ln.cantidad_solicitada)
        iva_total += calc["iva_importe"]
        ieps_total += calc["ieps_importe"]
        db.add(LineaRemision(
            tenant_id=ctx.tenant_id,
            remision_id=rem.id,
            numero_linea=i,
            producto_id=ln.producto_id,
            presentacion=ln.presentacion,
            cantidad_solicitada=ln.cantidad_solicitada,
            precio_unitario=precio,
            importe=importe,
            iva_importe=calc["iva_importe"],
            ieps_importe=calc["ieps_importe"],
            notas=ln.notas,
        ))
    rem.subtotal = subtotal
    rem.iva = iva_total
    rem.ieps = ieps_total
    rem.total = subtotal - payload.descuento + iva_total + ieps_total
    flush_or_conflict(db, detail=_DUP)
    # Reintento del cruce con el espejo: si la factura de SAE pasó por el
    # espejo ANTES de que esta remisión existiera (la sync solo re-manda ~3
    # días), el cruce factura→remisión ya no se vuelve a intentar — se intenta
    # aquí, al revés, con los mismos candados (caso HO-33APA-MAR / ZEHMOHOS 810).
    ligar_remision_con_espejo(db, rem)
    db.flush()
    db.refresh(rem)
    return rem


def _marcar_impresas(db: Session, rems: list[Remision]) -> None:
    """Deja el rastro de que estas remisiones ya salieron en papel.

    Desde aquí el cliente puede tener el documento firmado, así que el PATCH
    deja de aceptar cambios de partidas que vengan de una conexión (ver el
    candado en `update_remision`). Se estampa UNA vez: reimprimir no mueve la
    fecha, porque lo que importa es cuándo salió el primer papel.
    """
    ahora = func.now()
    for rem in rems:
        if rem.impresa_at is None:
            rem.impresa_at = ahora
    db.flush()


@router.get("/pdf")
def remisiones_pdf_lote(
    ids: str = Query(..., description="IDs de remisión separados por coma"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """PDF de varias remisiones (una por página) con el diseño de la factura.
    Definido ANTES de /{rem_id} para que la ruta estática gane."""
    id_list: list[UUID] = []
    for raw in ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            id_list.append(UUID(raw))
        except ValueError:
            continue
    if not id_list:
        raise HTTPException(status_code=422, detail="Sin remisiones para imprimir")
    if len(id_list) > 200:
        raise HTTPException(status_code=422, detail="Máximo 200 remisiones por PDF")
    q = db.query(Remision).filter(
        Remision.id.in_(id_list), Remision.deleted_at.is_(None)
    )
    if ctx.cliente_scope:
        q = q.filter(Remision.cliente_facturacion_id.in_(ctx.cliente_scope))
    rems = q.order_by(_FOLIO_PREFIJO, _FOLIO_NUMERO, Remision.folio_interno).all()
    if not rems:
        raise HTTPException(status_code=404, detail="No se encontraron remisiones")
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    por_rem = _nombres_para_pdf(db, rems)
    cli_ids = {r.cliente_facturacion_id for r in rems}
    clientes = {c.id: c for c in db.query(Cliente).filter(Cliente.id.in_(cli_ids)).all()}
    items = [(r, clientes.get(r.cliente_facturacion_id), por_rem[r.id]) for r in rems]
    pdf = build_remisiones_pdf(items, tenant)
    _marcar_impresas(db, rems)
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="remisiones.pdf"'},
    )


def _nombres_para_pdf(db: Session, rems: list[Remision]) -> dict:
    """{remision_id: {producto_id: texto impreso}} para PDFs y correos al cliente.

    El documento que el cliente firma trae SU nombre y SU clave del producto
    (producto_clientes — la misma capa que ya usa el CFDI en services/cfdi.py),
    con el nombre interno de respaldo. Formato: "CLAVE — NOMBRE DEL CLIENTE".
    Todo precargado en dos consultas: un lote de 200 remisiones no puede hacer
    un SELECT por línea.
    """
    prod_ids = {ln.producto_id for r in rems for ln in r.lineas}
    interno = dict(
        db.query(Producto.id, Producto.nombre).filter(Producto.id.in_(prod_ids)).all()
    ) if prod_ids else {}
    cli_ids = {r.cliente_facturacion_id for r in rems if r.cliente_facturacion_id}
    # Con clave por plaza (0067) puede haber fila genérica Y por sucursal para
    # el mismo (cliente, producto): la llave lleva la sucursal y cada remisión
    # resuelve con SU plaza — misma regla que el export (sucursal gana, cae la
    # genérica, la de otra plaza no se presta).
    pcs = {}
    if cli_ids and prod_ids:
        for pc in (
            db.query(ProductoCliente)
            .filter(
                ProductoCliente.cliente_id.in_(cli_ids),
                ProductoCliente.producto_id.in_(prod_ids),
            )
            .all()
        ):
            pcs[(pc.cliente_id, pc.producto_id, pc.sucursal_id)] = pc
    out: dict = {}
    for r in rems:
        nombres: dict = {}
        for ln in r.lineas:
            pc = (
                pcs.get((r.cliente_facturacion_id, ln.producto_id, r.sucursal_id))
                if r.sucursal_id is not None else None
            ) or pcs.get((r.cliente_facturacion_id, ln.producto_id, None))
            base = (pc.nombre_cliente or "").strip() if pc else ""
            base = base or interno.get(ln.producto_id) or str(ln.producto_id)
            codigo = (pc.codigo_cliente or "").strip() if pc else ""
            nombres[ln.producto_id] = f"{codigo} — {base}" if codigo else base
        out[r.id] = nombres
    return out


def _decorar_detalle(db: Session, ctx: AuthContext, rem: Remision) -> Remision:
    """El detalle como lo espera la pantalla: el nombre del producto por línea,
    la OC de la que vino y el preflight de claves SAE.

    Lo comparten el GET y los endpoints que devuelven la remisión YA modificada
    (cruzar una partida): si uno se lo saltara, el aviso «N partidas sin clave
    SAE» se apagaría solo al guardar —justo cuando hay que volver a mirarlo—
    hasta la siguiente recarga.
    """
    prod_ids = {ln.producto_id for ln in rem.lineas}
    names = dict(db.query(Producto.id, Producto.nombre).filter(Producto.id.in_(prod_ids)).all())
    for ln in rem.lineas:
        ln.producto_nombre = names.get(ln.producto_id)
    _adjuntar_oc(db, [rem])
    # Preflight de claves SAE: el conteo del encabezado Y la marca por línea,
    # con el mismo helper que valida el export para que los números casen.
    rem.sin_clave_sae = None
    rem.clave_no_en_sae = None
    if not rem.factura_sae and rem.estado != "CANCELADA":
        from ...services.export_sae import (
            _clave_para_remision, _claves_sae_de_clientes,
            lineas_clave_no_facturable, lineas_sin_clave,
        )

        faltan = lineas_sin_clave(db, ctx.tenant_id, [rem]).get(rem.id, [])
        if faltan:
            rem.sin_clave_sae = len(faltan)  # mismo conteo que la lista
            sin = set(faltan)
            for ln in rem.lineas:
                # Solo líneas VIVAS: una devolución total deja la línea en 0 y
                # no cuenta para el export — marcarla haría que el panel liste
                # más renglones que el conteo del encabezado.
                ln.sin_clave_sae = (
                    ln.producto_id in sin
                    and Decimal(str(ln.cantidad_solicitada or 0)) > 0
                )
        # La clave que la empresa de esta plaza no factura: misma idea, otro
        # arreglo — la clave existe, pero en la empresa equivocada.
        no_fact = lineas_clave_no_facturable(db, ctx.tenant_id, [rem]).get(rem.id, {})
        if no_fact:
            rem.clave_no_en_sae = len(no_fact)
            for ln in rem.lineas:
                dato = no_fact.get(ln.producto_id)
                if dato and Decimal(str(ln.cantidad_solicitada or 0)) > 0:
                    ln.clave_no_en_sae, ln.clave_de_baja_en_sae = dato
            pares = _claves_sae_de_clientes(
                db, ctx.tenant_id, {rem.cliente_facturacion_id}
            ).get(rem.cliente_facturacion_id, [])
            par, _c = _clave_para_remision(pares, rem.sucursal_id) if pares else (None, None)
            rem.empresa_sae = par[0] if par else None
    return rem


# OJO con el orden: esta ruta va ANTES de GET /{rem_id} — FastAPI casa en
# orden de declaración y "reporte-compras" parsearía como UUID (422).
@router.get("/reporte-compras")
def reporte_compras(
    fechas: str = Query(..., description="Fechas de ENTREGA, ISO, separadas por coma"),
    perfil: Optional[str] = Query(default=None, max_length=40),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """La materia prima de la lista de compras (21-sep-2026, meta 1).

    El comando de WhatsApp «lista de compras <días>» hoy pivotea el Master de
    Google Sheets. Este endpoint entrega LO MISMO desde las remisiones vivas:
    cuánto se pide de cada producto+presentación por fecha de entrega. La
    agregación por día y el pivote los sigue haciendo el bot — aquí solo viven
    los datos, para que el reporte no dependa de la hoja.

    `perfil` acota al universo del Master de ese perfil: las remisiones cuya OC
    entró con ancla EHMO:<perfil>:. Sin perfil van todas las remisiones con
    entrega en esas fechas — más de lo que el Master ve, no menos.

    La UNIDAD que viaja es lineas_remision.presentacion, que ya es canónica
    (KILO/PIEZA/...): la regla del dueño de nunca mezclar unidades se cumple
    aguas abajo agrupando por ella.
    """
    try:
        dias = sorted({date.fromisoformat(f.strip()) for f in fechas.split(",") if f.strip()})
    except ValueError:
        raise HTTPException(status_code=422, detail="fechas: ISO yyyy-mm-dd separadas por coma")
    if not dias or len(dias) > 14:
        raise HTTPException(status_code=422, detail="entre 1 y 14 fechas")

    q = (
        db.query(
            Producto.clave_sae,
            Producto.nombre,
            LineaRemision.presentacion,
            Remision.fecha_entrega,
            func.sum(LineaRemision.cantidad_solicitada).label("cantidad"),
            func.array_agg(func.distinct(func.coalesce(Remision.nota_entrega, ""))).label("hospitales"),
            func.count(func.distinct(Remision.id)).label("remisiones"),
            func.count().label("partidas"),
        )
        .join(LineaRemision, LineaRemision.remision_id == Remision.id)
        .join(Producto, Producto.id == LineaRemision.producto_id)
        .filter(
            Remision.deleted_at.is_(None),
            Remision.estado != "CANCELADA",
            Remision.fecha_entrega.in_(dias),
            LineaRemision.cantidad_solicitada > 0,
        )
    )
    # LA VERSIÓN QUE MANDA ES LA DEL DOCUMENTO (21-sep-2026). Cuando una OC
    # recibió una versión posterior que nadie ha aplicado (incidencia abierta),
    # las líneas de la remisión son las VIEJAS — y una lista de compras armada
    # con ellas compra de menos exactamente donde el cliente cambió el pedido.
    # Medido el día que se escribió: 6 productos y ~11 partidas de diferencia
    # contra el Master en una sola corrida de dos días. Para esas remisiones se
    # usan las líneas del documento nuevo (texto, sin cruzar) y las capturadas
    # se excluyen; el que arma el pivote ya agrupa por clave-o-nombre y
    # canoniza unidades, así que las dos fuentes conviven.
    con_cambio = (
        db.query(OCRecibida.remision_id, OCRecibida.payload_nuevo, Remision.fecha_entrega,
                 func.coalesce(Remision.nota_entrega, "").label("hospital"))
        .join(Remision, Remision.id == OCRecibida.remision_id)
        .filter(
            Remision.deleted_at.is_(None),
            Remision.estado != "CANCELADA",
            Remision.fecha_entrega.in_(dias),
            OCRecibida.cambio_detectado_at.isnot(None),
            OCRecibida.cambio_resuelto_at.is_(None),
            OCRecibida.payload_nuevo.isnot(None),
        )
    )
    if perfil:
        con_cambio = con_cambio.filter(OCRecibida.origen_externo.like(f"EHMO:{perfil}:%"))
    pendientes = con_cambio.all()
    ids_pendientes = {r.remision_id for r in pendientes}

    if ids_pendientes:
        q = q.filter(~Remision.id.in_(ids_pendientes))
    if perfil:
        q = q.join(OCRecibida, OCRecibida.remision_id == Remision.id).filter(
            OCRecibida.origen_externo.like(f"EHMO:{perfil}:%")
        )
    q = q.group_by(Producto.clave_sae, Producto.nombre,
                   LineaRemision.presentacion, Remision.fecha_entrega)

    filas = [
        {
            "clave": r.clave_sae,
            "descripcion": r.nombre,
            "unidad": r.presentacion,
            "fecha": r.fecha_entrega.isoformat(),
            "cantidad": str(r.cantidad),
            "hospitales": sorted(h for h in (r.hospitales or []) if h),
            "remisiones": r.remisiones,
            "partidas": r.partidas,
        }
        for r in q.all()
    ]
    for r in pendientes:
        for ln in (r.payload_nuevo or {}).get("lineas") or []:
            if not isinstance(ln, dict):
                continue
            filas.append({
                "clave": (ln.get("clave") or "").strip() or None,
                "descripcion": (ln.get("descripcion") or "").strip() or "PARTIDA",
                # unidad del DOCUMENTO, texto del cliente: el bot la canoniza
                "unidad": (ln.get("unidad") or "").strip() or "?",
                "fecha": r.fecha_entrega.isoformat(),
                "cantidad": str(ln.get("cantidad") or 0),
                "hospitales": [r.hospital] if r.hospital else [],
                "remisiones": 1,
                "partidas": 1,
                # La marca de honestidad: esta cantidad viene del documento que
                # nadie ha aplicado, no de la captura. La incidencia sigue
                # abierta y el reporte no la resuelve — solo no compra de menos.
                "documento_nuevo": True,
            })
    return {"filas": filas, "fechas": [d.isoformat() for d in dias],
            "remisiones": sum(f["remisiones"] for f in filas),
            "con_cambio_abierto": len(pendientes)}


@router.get("/reporte-armado")
def reporte_armado(
    fechas: Optional[str] = Query(default=None, description="Fechas de ENTREGA (bodega), ISO, separadas por coma"),
    folios: Optional[str] = Query(default=None, description="OC del cliente (su_pedido), separadas por coma"),
    origen: Optional[str] = Query(default=None, max_length=200,
                                  description="Prefijos de origen_externo (csv): WA:,EMAIL: = carril Balles/Jubrán; EHMO:<perfil>: = un Master de EHMO"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """La materia prima de la hoja de armado (21-sep-2026, meta 1).

    El comando «hoja de armado» pivotea el Master de Google Sheets: filas =
    producto (descripción · unidad · nota), columnas = OC del cliente, celdas =
    suma de cantidad. Este endpoint entrega el detalle POR REMISIÓN — folio del
    cliente, cliente, fecha de bodega y las líneas con su categoría — y el
    pivote, el PDF y los bloques por categoría los sigue armando el bot, igual
    que con la lista de compras.

    Dos filtros excluyentes, como el comando: por fecha de bodega
    (fecha_entrega) o por lista de OC (su_pedido); la lista de OC manda.

    `origen` acota al CARRIL de un Master: el de Balles/Jubrán solo ve las OC
    que entraron por WhatsApp/correo (WA:, EMAIL:) y el de EHMO las suyas —
    sin él, la hoja de armado de un carril se llevaría las entregas del otro
    (medido 21-sep-2026: 21 remisiones de hospitales dentro de la hoja de
    Balles). Aplica al filtro por fecha y al aviso; la lista de OC ya es
    explícita y no lo necesita.

    Dos avisos viajan con el reporte, los dos por la misma razón: que nada se
    caiga de la hoja en silencio. `sin_remision` son las OC de la bandeja que
    ese día nunca se cruzaron (no hay qué armar aunque el cliente lo pidió), y
    `sin_fecha` viaja siempre que se filtra por fecha: una remisión sin
    fecha_entrega no casa con NINGÚN día y se caería de la hoja en silencio —
    el mismo hoyo que en la hoja vieja tuvo a la 24973 fuera con $50,633.78
    dentro (13-ago-2026). La ventana es de 35 días hacia atrás: el proxy más
    honesto del «periodo activo» de la hoja, que aquí no existe.
    """
    quiere = {normalizar_folio(f) for f in (folios or "").split(",") if f.strip()}
    if len(quiere) > 40:
        raise HTTPException(status_code=422, detail="máximo 40 folios")
    dias = []
    if not quiere:
        try:
            dias = sorted({date.fromisoformat(f.strip()) for f in (fechas or "").split(",") if f.strip()})
        except ValueError:
            raise HTTPException(status_code=422, detail="fechas: ISO yyyy-mm-dd separadas por coma")
        if not dias:
            raise HTTPException(status_code=422, detail="hace falta `fechas` o `folios`")
        if len(dias) > 14:
            raise HTTPException(status_code=422, detail="entre 1 y 14 fechas")

    base = [Remision.deleted_at.is_(None), Remision.estado != "CANCELADA"]
    prefijos = [x.strip() for x in (origen or "").split(",") if x.strip()]
    if prefijos and not quiere:
        # EXISTS y no JOIN: una remisión con más de una OC saldría doble
        # `correlate(Remision)` explícito: sin él, la consulta de incidencias
        # —que ya une OCRecibida— autocorrelaciona las dos tablas y el EXISTS
        # se queda sin FROM (SQLAlchemy lanza InvalidRequestError).
        base.append(
            db.query(OCRecibida.id)
            .filter(OCRecibida.remision_id == Remision.id,
                    or_(*[OCRecibida.origen_externo.like(pf.replace("%", "") + "%")
                          for pf in prefijos]))
            .correlate(Remision)
            .exists()
        )
    base = tuple(base)
    if quiere:
        # su_pedido se guarda COMO LLEGÓ, y llega relleno de ceros desde SAE
        # ('0000025587' — medido 21-sep-2026, con el filtro comparando contra
        # '25587' no casaba ni una). Se compara sin ceros de los dos lados; el
        # folio con serie ('HO-39SAL-LUN') no tiene ceros que quitar y cae en la
        # comparación literal.
        filtro = (or_(func.ltrim(Remision.su_pedido, "0").in_(quiere),
                      Remision.su_pedido.in_(quiere)),)
    else:
        filtro = (Remision.fecha_entrega.in_(dias),)

    q = (
        db.query(
            Remision.id,
            Remision.su_pedido,
            Remision.folio_interno,
            Remision.fecha_entrega,
            Remision.nota_entrega,
            Cliente.legal_name,
            Producto.clave_sae,
            Producto.nombre,
            CategoriaProducto.nombre.label("categoria"),
            LineaRemision.presentacion,
            LineaRemision.notas,
            LineaRemision.cantidad_solicitada,
        )
        .join(LineaRemision, LineaRemision.remision_id == Remision.id)
        .join(Producto, Producto.id == LineaRemision.producto_id)
        .join(Cliente, Cliente.id == Remision.cliente_facturacion_id)
        .outerjoin(CategoriaProducto, CategoriaProducto.id == Producto.categoria_id)
        .filter(*base, *filtro, LineaRemision.cantidad_solicitada > 0)
    )

    # LA VERSIÓN QUE MANDA ES LA DEL DOCUMENTO — misma regla que la lista de
    # compras (21-sep-2026): con una incidencia de cambio abierta, las líneas
    # capturadas son las VIEJAS, y una hoja de armado armada con ellas surte de
    # menos justo donde el cliente cambió el pedido. Esas remisiones viajan con
    # las líneas del documento nuevo, marcadas, y las capturadas se excluyen.
    pendientes = {
        r.remision_id: r.payload_nuevo
        for r in db.query(OCRecibida.remision_id, OCRecibida.payload_nuevo)
        .join(Remision, Remision.id == OCRecibida.remision_id)
        .filter(
            *base, *filtro,
            OCRecibida.cambio_detectado_at.isnot(None),
            OCRecibida.cambio_resuelto_at.is_(None),
            OCRecibida.payload_nuevo.isnot(None),
        )
        .all()
    }

    rems: dict = {}
    for r in q.all():
        rem = rems.setdefault(r.id, {
            "folio": normalizar_folio(r.su_pedido) or r.folio_interno,
            "cliente": r.legal_name or "",
            "bodega": r.fecha_entrega.isoformat() if r.fecha_entrega else None,
            # el punto de entrega (hospital en EHMO): columna del armado de ese carril
            "hospital": r.nota_entrega or "",
            "documento_nuevo": r.id in pendientes,
            "lineas": [],
        })
        if r.id in pendientes:
            continue          # sus líneas salen del documento, abajo
        rem["lineas"].append({
            "clave": r.clave_sae,
            "descripcion": r.nombre,
            "unidad": r.presentacion,
            "nota": r.notas or "",
            "cantidad": str(r.cantidad_solicitada),
            "categoria": r.categoria,
        })

    if pendientes:
        # La categoría de una línea del documento se resuelve por su clave —
        # si no trae o no casa, va sin categoría (cae al bloque RESTO del bot).
        claves_doc = {
            (ln.get("clave") or "").strip().upper()
            for pn in pendientes.values() for ln in (pn or {}).get("lineas") or []
            if isinstance(ln, dict) and (ln.get("clave") or "").strip()
        }
        cat_por_clave = {}
        if claves_doc:
            for p, cat in (
                db.query(Producto, CategoriaProducto.nombre)
                .outerjoin(CategoriaProducto, CategoriaProducto.id == Producto.categoria_id)
                .filter(func.upper(Producto.clave_sae).in_(claves_doc))
                .all()
            ):
                cat_por_clave[p.clave_sae.upper()] = cat
        for rid, pn in pendientes.items():
            rem = rems.get(rid)
            if rem is None:
                continue
            for ln in (pn or {}).get("lineas") or []:
                if not isinstance(ln, dict):
                    continue
                clave = (ln.get("clave") or "").strip() or None
                rem["lineas"].append({
                    "clave": clave,
                    "descripcion": (ln.get("descripcion") or "").strip() or "PARTIDA",
                    # unidad del DOCUMENTO, texto del cliente: el bot la canoniza
                    "unidad": (ln.get("unidad") or "").strip() or "?",
                    "nota": (ln.get("notas") or ln.get("nota") or "").strip(),
                    "cantidad": str(ln.get("cantidad") or 0),
                    "categoria": cat_por_clave.get(clave.upper()) if clave else None,
                })

    # EL HUECO QUE NO SE VE: una OC que entró a la bandeja y nunca se cruzó no
    # tiene remisión, así que no está en ninguna consulta de arriba — y la hoja
    # sale sin ella sin decir nada. Medido al migrar (21-sep-2026): 116 de las
    # 224 órdenes del Master del periodo estaban así. Es el mismo modo de fallo
    # que `sin_fecha`, una capa antes: ahí la remisión existe sin fecha, aquí no
    # existe la remisión. La fecha de entrega del documento viaja en el payload
    # en ISO (lo escribe el bot), así que se compara directo.
    sin_remision = []
    if dias:
        q_sr = (
            db.query(OCRecibida, Cliente.legal_name)
            .outerjoin(Cliente, Cliente.id == OCRecibida.cliente_id)
            .filter(
                OCRecibida.remision_id.is_(None),
                OCRecibida.estado != "DESCARTADA",
                OCRecibida.payload["fecha_entrega"].astext.in_([d.isoformat() for d in dias]),
            )
        )
        if prefijos:
            q_sr = q_sr.filter(or_(*[OCRecibida.origen_externo.like(pf.replace("%", "") + "%")
                                     for pf in prefijos]))
        for oc, nombre in q_sr.all():
            sin_remision.append({
                "folio": normalizar_folio(oc.folio_externo) or "?",
                "cliente": nombre or (oc.payload or {}).get("cliente_nombre") or "",
                "estado": oc.estado,
                "partidas": len([x for x in ((oc.payload or {}).get("lineas") or [])
                                 if isinstance(x, dict)]),
                "bodega": (oc.payload or {}).get("fecha_entrega"),
            })

    sin_fecha = []
    if dias:
        for r in (
            db.query(Remision.su_pedido, Remision.folio_interno, Cliente.legal_name,
                     Remision.total, func.count(LineaRemision.id).label("partidas"))
            .join(Cliente, Cliente.id == Remision.cliente_facturacion_id)
            .outerjoin(LineaRemision, LineaRemision.remision_id == Remision.id)
            .filter(*base, Remision.fecha_entrega.is_(None),
                    Remision.fecha_remision >= date.today() - timedelta(days=35))
            .group_by(Remision.id, Cliente.legal_name)
            .all()
        ):
            sin_fecha.append({
                "folio": normalizar_folio(r.su_pedido) or r.folio_interno,
                "cliente": r.legal_name or "",
                "partidas": r.partidas,
                "total": str(r.total or 0),
            })

    return {
        "remisiones": sorted(rems.values(), key=lambda x: x["folio"]),
        "sin_fecha": sorted(sin_fecha, key=lambda x: x["folio"]),
        "sin_remision": sorted(sin_remision, key=lambda x: x["folio"]),
        "con_cambio_abierto": len(pendientes),
    }


@router.get("/reporte-sin-precio")
def reporte_sin_precio(
    fechas: Optional[str] = Query(default=None, description="Fechas de ENTREGA, ISO, separadas por coma"),
    dias: int = Query(default=14, ge=1, le=90, description="Si no hay fechas: últimos N días de remisión"),
    origen: Optional[str] = Query(default=None, max_length=200),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Lo que hoy NO se puede facturar bien: sin clave en SAE, o sin precio.

    Es el cuarto reporte que sale del Master (21-sep-2026, meta 1). El de la
    hoja resuelve cada producto contra el catálogo de SAE en vivo; este contesta
    con lo que el Facturador ya sabe: el espejo de claves (`claves_sae`, las
    cuatro empresas) y el precio con el que se capturó la partida.

    Cuatro motivos, y se separan porque el arreglo de cada uno es distinto:
      · SIN CLAVE — la partida no cruzó con ningún producto, o el producto no
        tiene clave: hay que cruzarla o darla de alta.
      · CLAVE NO EN SAE — la clave existe aquí pero la empresa de esa plaza no
        la conoce (la 02 la tiene, la 03 no): hay que darla de alta ALLÁ.
      · CLAVE DE BAJA — existe en esa empresa pero dada de baja: se reactiva.
      · SIN PRECIO — la partida se capturó en $0: hay que ponerle precio.

    Un producto puede tener dos motivos a la vez; se reporta una vez por motivo,
    porque son dos trabajos distintos para dos personas distintas.
    """
    base = [Remision.deleted_at.is_(None), Remision.estado != "CANCELADA"]
    prefijos = [x.strip() for x in (origen or "").split(",") if x.strip()]
    if prefijos:
        base.append(
            db.query(OCRecibida.id)
            .filter(OCRecibida.remision_id == Remision.id,
                    or_(*[OCRecibida.origen_externo.like(pf.replace("%", "") + "%")
                          for pf in prefijos]))
            .correlate(Remision)
            .exists()
        )
    if fechas and fechas.strip():
        try:
            dset = sorted({date.fromisoformat(f.strip()) for f in fechas.split(",") if f.strip()})
        except ValueError:
            raise HTTPException(status_code=422, detail="fechas: ISO yyyy-mm-dd separadas por coma")
        if not dset or len(dset) > 31:
            raise HTTPException(status_code=422, detail="entre 1 y 31 fechas")
        base.append(Remision.fecha_entrega.in_(dset))
        ventana = {"fechas": [d.isoformat() for d in dset]}
    else:
        desde = date.today() - timedelta(days=dias)
        base.append(Remision.fecha_remision >= desde)
        ventana = {"desde": desde.isoformat(), "dias": dias}

    rems = db.query(Remision).filter(*base).all()
    if not rems:
        return {"productos": [], "remisiones": 0, "sin_clave": 0, "sin_precio": 0, **ventana}

    from ...services.export_sae import lineas_clave_no_facturable

    no_fact = lineas_clave_no_facturable(db, ctx.tenant_id, rems)
    por_id = {r.id: r for r in rems}
    filas = (
        db.query(LineaRemision, Producto)
        .outerjoin(Producto, Producto.id == LineaRemision.producto_id)
        .filter(LineaRemision.remision_id.in_(list(por_id)),
                LineaRemision.cantidad_solicitada > 0)
        .all()
    )

    # agrupado por (motivo, producto): el reporte es una lista de TRABAJOS, no de
    # renglones — el mismo producto en diez entregas es un solo trabajo.
    acc: dict = {}

    def anota(motivo, clave, descripcion, unidad, cantidad, folio):
        k = (motivo, (clave or "").upper(), (descripcion or "").upper())
        d = acc.setdefault(k, {"motivo": motivo, "clave": clave or None,
                               "descripcion": descripcion or "", "unidad": unidad or "",
                               "cantidad": Decimal("0"), "folios": []})
        d["cantidad"] += Decimal(str(cantidad or 0))
        if folio and folio not in d["folios"]:
            d["folios"].append(folio)

    for ln, prod in filas:
        rem = por_id.get(ln.remision_id)
        folio = (normalizar_folio(rem.su_pedido) if rem and rem.su_pedido
                 else (rem.folio_interno if rem else None))
        desc = (prod.nombre if prod else None) or (ln.notas or "").strip() or "PARTIDA SIN PRODUCTO"
        clave = (prod.clave_sae or "").strip() if prod else ""
        if not clave:
            anota("SIN CLAVE", None, desc, ln.presentacion, ln.cantidad_solicitada, folio)
        else:
            dato = (no_fact.get(ln.remision_id) or {}).get(ln.producto_id)
            if dato:
                _c, de_baja = dato
                anota("CLAVE DE BAJA" if de_baja else "CLAVE NO EN SAE", clave, desc,
                      ln.presentacion, ln.cantidad_solicitada, folio)
        if Decimal(str(ln.precio_unitario or 0)) <= 0:
            anota("SIN PRECIO", clave or None, desc, ln.presentacion,
                  ln.cantidad_solicitada, folio)

    # Las partidas que NUNCA cruzaron no son líneas: viven en la remisión como
    # pendientes de cruce, y son las que más duelen (nadie las ve hasta facturar).
    for r in rems:
        for p in (r.partidas_por_cruzar or []):
            if not isinstance(p, dict):
                continue
            anota("SIN CLAVE", (p.get("clave") or "").strip() or None,
                  (p.get("descripcion") or "").strip() or "PARTIDA SIN CRUZAR",
                  (p.get("unidad") or "").strip(), p.get("cantidad") or 0,
                  normalizar_folio(r.su_pedido) if r.su_pedido else r.folio_interno)

    productos = sorted(
        ({**d, "cantidad": str(d["cantidad"]), "n_folios": len(d["folios"]),
          "folios": d["folios"][:6]} for d in acc.values()),
        key=lambda x: (x["motivo"], -x["n_folios"], x["descripcion"]),
    )
    return {
        "productos": productos,
        "remisiones": len(rems),
        "sin_clave": sum(1 for p in productos if p["motivo"].startswith("SIN CLAVE")),
        "sin_precio": sum(1 for p in productos if p["motivo"] == "SIN PRECIO"),
        "clave_fuera_de_sae": sum(1 for p in productos
                                  if p["motivo"] in ("CLAVE NO EN SAE", "CLAVE DE BAJA")),
        **ventana,
    }


@router.get("/{rem_id}", response_model=RemisionDetailOut)
def get_remision(
    rem_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    rem = get_or_404(db, Remision, rem_id)
    if not ctx.cliente_permitido(rem.cliente_facturacion_id):
        raise HTTPException(status_code=404, detail="Remisión no encontrada")
    return _decorar_detalle(db, ctx, rem)


def _liberar_reservas(db: Session, ctx: AuthContext, rem: Remision, *, motivo: str) -> None:
    """Restituye al inventario la salida de una remisión CONFIRMADA (disponible
    sube exactamente lo que salió, según el stamp de cada línea) y registra un
    movimiento por línea. Lo usan cancelar y la reedición de una confirmada
    (que luego vuelve a descontar)."""
    prod_ids = {ln.producto_id for ln in rem.lineas if ln.lote_id is not None}
    productos = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(prod_ids)).all()}
    lotes = lotes_for_update(db, (ln.lote_id for ln in rem.lineas))
    for ln in rem.lineas:
        if ln.lote_id is None:
            continue
        lote = lotes.get(ln.lote_id)
        if lote is None:
            continue
        # Libera exactamente lo reservado al confirmar (unidad base guardada);
        # para filas antiguas sin cantidad_surtida, usa la estimación.
        if ln.cantidad_surtida is not None:
            cantidad = ln.cantidad_surtida
        else:
            factor = presentacion_factor(productos.get(ln.producto_id), ln.presentacion)
            cantidad = ln.cantidad_solicitada * factor
        lote.cantidad_disponible = lote.cantidad_disponible + cantidad
        db.add(build_movimiento(
            ctx.tenant_id, ctx.user_id, lote, "CANCELACION_REMISION", cantidad,
            ref_tipo="REMISION", ref_id=rem.id, motivo=motivo,
        ))
        # Limpia el vínculo de reserva: la línea ya no reserva nada. Evita
        # reservas huérfanas y cualquier doble-liberación futura.
        ln.lote_id = None
        ln.cantidad_surtida = None


def _exigir_editable(db: Session, rem: Remision) -> None:
    """Las dos puertas que comparten editar y cruzar una partida.

    Una FACTURADA o CANCELADA no se toca, y tampoco una que esté por detrás de
    una factura viva (BORRADOR o TIMBRADA): cambiarle una línea desincronizaría
    un comprobante ya emitido. Solo pasa la que no tiene factura o cuya última
    fue CANCELADA.
    """
    if rem.estado not in ("BORRADOR", "RESERVADO", "CONFIRMADA"):
        raise HTTPException(
            status_code=409,
            detail="Solo se puede editar una remisión en borrador, reservada o confirmada",
        )
    if rem.factura_id is not None:
        fac = db.query(Factura).filter(Factura.id == rem.factura_id).one_or_none()
        if fac is not None and fac.estado != "CANCELADA":
            raise HTTPException(
                status_code=409,
                detail="La remisión está ligada a una factura; cancélala o descártala antes de editar",
            )

def _exigir_no_exportada(rem: Remision) -> None:
    # LO QUE YA SALIÓ EN UN ARCHIVO DE SAE SE CONGELA (21-sep-2026, decisión del
    # dueño: congela —no solo avisa—, el encabezado también, y vale para
    # personas igual que para conexiones).
    #
    # Sin esto, una remisión exportada seguía siendo editable: pasaba el filtro
    # de estado porque BORRADOR está en la lista blanca, pasaba el de factura
    # porque `factura_id` es NULL, pasaba el de impresión porque `impresa_at` es
    # NULL, y aterrizaba en el DELETE de todas las partidas. Medido ese día: 41
    # remisiones alcanzables por $360,152. El hueco no se había ejercido —esto
    # es prevención, no limpieza.
    #
    # Va aquí y no en el PATCH a propósito. (a) `_exigir_editable` la comparten
    # editar y CRUZAR una partida, y repuntar una línea a otro producto
    # desincroniza el archivo igual que reescribirla. (b) El candado de la
    # impresión (más abajo) solo mira `lineas_in`, y por esa puerta se podía
    # cambiar cliente, descuento y fechas de una remisión ya exportada sin tocar
    # una sola línea — y el cliente y los importes son justo lo que viajó en el
    # archivo (export_sae.py:553-554).
    #
    # LA EXCEPCIÓN, y es una sola: sellar `factura_sae`. Eso NO es editar el
    # documento, es ACUSAR lo que SAE hizo con él — y es la única llave que
    # existe: `export_sae_at` solo se limpia cuando el espejo confirma que SAE
    # canceló esa factura (facturas.py:1467), y sin poder sellarla primero la
    # remisión quedaría congelada para siempre, incluso las que sí tienen salida.
    # Congelar el acuse convertiría el candado en una trampa. El PATCH la deja
    # pasar cuando el cuerpo trae SOLO ese campo.
    #
    # Lo que sigue sin llave: `export_pedido_at` no se limpia en ningún lado.
    # Cinco de las 41 congeladas hoy son solo-pedido y no tienen salida; eso
    # necesita una regla del dueño y está reportado aparte.
    marca = rem.export_sae_at or rem.export_pedido_at
    if marca is not None:
        cual = "el masivo de SAE" if rem.export_sae_at else "un pedido de SAE"
        raise HTTPException(
            status_code=409,
            detail=(
                f"La remisión {rem.folio_interno} ya salió en {cual} "
                f"({marca:%d/%m/%Y %H:%M}): su contenido quedó congelado para que el "
                "archivo y el Facturador no cuenten dos historias de la misma venta. "
                "Si de verdad cambió, cancela el documento en SAE."
            ),
        )


@router.patch("/{rem_id}", response_model=RemisionDetailOut)
def update_remision(
    rem_id: UUID,
    payload: RemisionUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    rem = get_or_404(db, Remision, rem_id)
    _exigir_editable(db, rem)
    era_confirmada = rem.estado == "CONFIRMADA"
    almacen_anterior = rem.almacen_id           # para detectar cambio de almacén
    data = payload.model_dump(exclude_unset=True)
    # El acuse de SAE pasa aunque esté congelada; cualquier otra cosa, no.
    # `permitir_negativos` no es un campo del documento (es una autorización de
    # sobregiro), así que no cuenta para decidir si esto es solo un acuse.
    tocados = set(data) - {"permitir_negativos"}
    if tocados != {"factura_sae"}:
        _exigir_no_exportada(rem)
    lineas_in = data.pop("lineas", None)
    # UNA REMISIÓN IMPRESA NO LA REESCRIBE UNA SINCRONIZACIÓN (16-sep-2026).
    # El vigía del bot pisó nueve remisiones de la semana 38 que ya estaban
    # impresas y firmadas: les devolvió las cantidades del pedido encima de los
    # pesos de báscula y les volvió a meter partidas que el cliente no recibió.
    # Con el papel ya en la calle, las partidas solo las mueve una PERSONA, que
    # es quien puede hablar con el cliente; una conexión (`conexion_id`) no
    # tiene con qué decidir eso. El resto del PATCH —fecha de entrega, notas—
    # sigue pasando: lo que se congela es el detalle que el cliente firmó.
    if lineas_in is not None and rem.impresa_at is not None and ctx.conexion_id is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"La remisión {rem.folio_interno} ya se imprimió "
                f"({rem.impresa_at:%d/%m/%Y %H:%M}) y el cliente tiene ese papel: "
                "sus partidas ya no se sincronizan solas. Si lo entregado cambió, "
                "corrígelo en el Facturador."
            ),
        )
    # `exclude_unset` también poda las líneas: una que no mande `presentacion`
    # (tiene default "KILO" en el schema, así que omitirla es válido) salía del
    # dict sin la llave y el endpoint reventaba con KeyError. El pop de arriba
    # conserva la distinción "no mandó líneas" vs "las mandó"; si las mandó, se
    # vuelven a volcar CON sus defaults.
    if lineas_in is not None:
        lineas_in = [ln.model_dump() for ln in payload.lineas]
    permitir_negativos = bool(data.pop("permitir_negativos", False))
    # «Dar por revisada» no es un campo más: solo se puede APAGAR (la marca la
    # pone la bandeja al pasar la orden sin revisar, no se enciende a mano) y no
    # se apaga mientras queden partidas sin cruzar — son justamente lo que
    # faltaba por hacer.
    revisada = data.pop("revision_pendiente", None)
    almacen_cambio = "almacen_id" in data and data["almacen_id"] != almacen_anterior

    if data.get("almacen_id") is not None:
        ensure_fk(db, Almacen, data["almacen_id"], "almacen_id")
    if data.get("lista_precios_id") is not None:
        ensure_fk(db, ListaPrecios, data["lista_precios_id"], "lista_precios_id")
    if data.get("proyecto_id") is not None:
        ensure_fk(db, Proyecto, data["proyecto_id"], "proyecto_id")
    if data.get("cliente_facturacion_id") is not None:
        ensure_fk(db, Cliente, data["cliente_facturacion_id"], "cliente_facturacion_id")

    # Cliente/sucursal coherentes (el cliente debe surtirse de la plaza). Se
    # valida también la sucursal HEREDADA: cambiar solo el cliente no debe dejar
    # una plaza que no lo surte (precio/serie se resolverían con datos mezclados).
    nuevo_cliente = data.get("cliente_facturacion_id", rem.cliente_facturacion_id)
    sucursal_efectiva = data["sucursal_id"] if "sucursal_id" in data else rem.sucursal_id
    if sucursal_efectiva is not None and ("sucursal_id" in data or "cliente_facturacion_id" in data):
        get_or_404(db, Sucursal, sucursal_efectiva)
        if not es_sucursal_de(db, sucursal_efectiva, nuevo_cliente):
            if "sucursal_id" in data:
                raise HTTPException(status_code=422, detail="El cliente de la remisión no se surte de esa sucursal")
            raise HTTPException(
                status_code=422,
                detail="El nuevo cliente no se surte de la sucursal actual; cámbiala o quítala en la misma edición",
            )

    for key, value in data.items():
        setattr(rem, key, value)

    if revisada is False:
        if rem.partidas_por_cruzar:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Quedan {len(rem.partidas_por_cruzar)} partidas de la orden sin cruzar; "
                    "agrégalas como líneas o descártalas antes de dar la remisión por revisada"
                ),
            )
        rem.revision_pendiente = False

    # El folio de SAE manda sobre el estado mientras la remisión no haya salido:
    # ponerlo la reserva, quitarlo la regresa a borrador. Una CONFIRMADA o
    # FACTURADA no retrocede — ahí el folio es solo un dato más.
    if "su_pedido" in data:
        rem.su_pedido = (data["su_pedido"] or "").strip() or None
    if "factura_sae" in data:
        rem.factura_sae = (data["factura_sae"] or "").strip() or None
        if rem.factura_sae and rem.estado == "BORRADOR":
            rem.estado = "RESERVADO"
        elif not rem.factura_sae and rem.estado == "RESERVADO":
            rem.estado = "BORRADOR"

    # Cambio de almacén SIN reenviar líneas en una CONFIRMADA: la reserva vive en
    # lotes del almacén anterior → se libera y se re-reserva en el nuevo (el mismo
    # tratamiento que el bloque de líneas hace vía `almacen_cambio`).
    if era_confirmada and almacen_cambio and lineas_in is None:
        _liberar_reservas(
            db, ctx, rem,
            motivo=f"Cambio de almacén remisión {rem.folio_interno} (libera reserva previa)",
        )
        db.flush()
        reservar_stock_remision(db, ctx, rem, permitir_negativos=permitir_negativos)

    # Reemplaza las líneas y recalcula el subtotal (mismo criterio que el alta).
    if lineas_in is not None:
        if not lineas_in:
            raise HTTPException(status_code=422, detail="La remisión debe tener al menos una línea")
        _ensure_productos(db, [ln["producto_id"] for ln in lineas_in])

        # ¿Cambió el detalle que AFECTA inventario (producto, presentación,
        # cantidad)? Si no, no se toca el inventario: se preserva la reserva
        # existente y solo se actualizan precios/notas. Esto evita retiros/
        # reservas no deseados al editar una CONFIRMADA sin mover cantidades.
        def _firma(pid, pres, cant) -> tuple:
            return (str(pid), str(pres), Decimal(str(cant)))
        firma_actual = sorted(_firma(l.producto_id, l.presentacion, l.cantidad_solicitada) for l in rem.lineas)
        firma_nueva = sorted(_firma(ln["producto_id"], ln["presentacion"], ln["cantidad_solicitada"]) for ln in lineas_in)
        # Cambió el inventario si cambian productos/cantidades O el almacén (la
        # reserva vive en un lote de un almacén concreto; mover almacén = re-reservar).
        inv_cambio = firma_actual != firma_nueva or almacen_cambio

        # Confirmada + cambio real de inventario → libera la reserva previa
        # (más abajo se re-reserva con las líneas nuevas). Si no cambia, indexa
        # la reserva por firma para heredarla en las líneas reconstruidas.
        reserva_por_firma: dict[tuple, list] = {}
        if era_confirmada and inv_cambio:
            _liberar_reservas(db, ctx, rem, motivo=f"Reedición remisión {rem.folio_interno} (libera reserva previa)")
        elif era_confirmada:
            for l in rem.lineas:
                reserva_por_firma.setdefault(
                    _firma(l.producto_id, l.presentacion, l.cantidad_solicitada), []
                ).append((l.lote_id, l.cantidad_surtida))

        # Borrado en bloque: `db.delete` por objeto emitía un viaje por línea
        # (psycopg2 no agrupa DELETE). "fetch" no es opcional: `_liberar_reservas`
        # deja las líneas sucias (les limpia lote_id/cantidad_surtida) y sin
        # reconciliar la sesión el flush intentaría un UPDATE sobre filas ya
        # borradas → StaleDataError. El flush sigue siendo necesario ANTES de
        # insertar las nuevas por el unique (remision_id, numero_linea).
        db.query(LineaRemision).filter(
            LineaRemision.remision_id == rem.id
        ).delete(synchronize_session="fetch")
        db.expire(rem, ["lineas"])
        db.flush()
        fiscal = _fiscal_por_producto(db, [ln["producto_id"] for ln in lineas_in])
        precios = _precios_de_lineas(db, rem, lineas_in)
        subtotal = _ZERO
        iva_total = _ZERO
        ieps_total = _ZERO
        for i, (ln, precio) in enumerate(zip(lineas_in, precios, strict=True), start=1):
            importe = ln["cantidad_solicitada"] * precio
            subtotal += importe
            prod, esq = fiscal.get(ln["producto_id"], (None, None))
            calc = calcular_linea_producto(prod, esq, importe, ln["cantidad_solicitada"])
            iva_total += calc["iva_importe"]
            ieps_total += calc["ieps_importe"]
            nueva = LineaRemision(
                tenant_id=ctx.tenant_id, remision_id=rem.id, numero_linea=i,
                producto_id=ln["producto_id"], presentacion=ln["presentacion"],
                cantidad_solicitada=ln["cantidad_solicitada"], precio_unitario=precio,
                importe=importe, iva_importe=calc["iva_importe"],
                ieps_importe=calc["ieps_importe"], notas=ln.get("notas"),
            )
            # Inventario sin cambios: hereda la reserva de la línea equivalente.
            if era_confirmada and not inv_cambio:
                heredadas = reserva_por_firma.get(_firma(ln["producto_id"], ln["presentacion"], ln["cantidad_solicitada"]))
                if heredadas:
                    nueva.lote_id, nueva.cantidad_surtida = heredadas.pop()
            db.add(nueva)
        rem.subtotal = subtotal
        rem.iva = iva_total
        rem.ieps = ieps_total
        # Reedición de una CONFIRMADA con cambio de inventario: re-reserva con
        # las líneas nuevas (queda CONFIRMADA). Sin existencia → 422 y revierte.
        if era_confirmada and inv_cambio:
            db.flush()
            db.refresh(rem)                          # recarga rem.lineas con las nuevas
            reservar_stock_remision(db, ctx, rem, permitir_negativos=permitir_negativos)

    rem.total = (rem.subtotal or _ZERO) - (rem.descuento or _ZERO) + (rem.iva or _ZERO) + (rem.ieps or _ZERO)
    # La edición puede completar la llave del cruce con el espejo (su_pedido o
    # cliente nuevos, un precio corregido que ya cuadra): se reintenta aquí.
    # Solo actúa sobre una remisión libre — RESERVADO/FACTURADA no entran.
    ligar_remision_con_espejo(db, rem)
    rem.updated_by = ctx.user_id
    db.flush()
    db.refresh(rem)
    return rem


@router.get("/{rem_id}/claves-sae", response_model=ClavesSaeOut)
def claves_sae_de_remision(
    rem_id: UUID,
    q: str = Query("", max_length=80),
    producto_id: Optional[UUID] = Query(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Las claves que conoce la empresa de SAE de ESTA remisión. La lógica vive
    en services/claves_sae.py porque la captura pregunta lo mismo sin tener
    todavía una remisión que consultar."""
    from ...services.claves_sae import sugerencias_de_claves

    rem = get_or_404(db, Remision, rem_id)
    if not ctx.cliente_permitido(rem.cliente_facturacion_id):
        raise HTTPException(status_code=404, detail="Remisión no encontrada")
    return sugerencias_de_claves(
        db, ctx.tenant_id, rem.cliente_facturacion_id, rem.sucursal_id, q, producto_id
    )


@router.post("/{rem_id}/lineas/{linea_id}/cruzar", response_model=RemisionDetailOut)
def cruzar_linea(
    rem_id: UUID,
    linea_id: UUID,
    payload: CruzarLineaIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Re-apunta UNA partida a otro producto del catálogo, sin re-capturar.

    El caso que lo pide: el aviso «N partidas sin clave SAE del cliente». La
    partida cruzó a un producto que ese cliente no tiene en su inventario de
    SAE, y el arreglo no siempre es inventarle una clave —eso es dar de alta un
    artículo en SAE por la puerta de atrás—: casi siempre la partida va al
    producto que el cliente SÍ conoce, y lo que falla es el cruce.

    Se edita la línea EN SU SITIO (mismo id, mismo número, su nota y su
    devolución intactas) en vez de reenviar todas por el PATCH, que las borra y
    las vuelve a insertar: ahí se perdían las notas de «qué revisar» y una
    partida devuelta al 100% (cantidad 0) ni siquiera podía viajar de vuelta.

    Lo que NO decide solo: el precio. Cambiar el producto no autoriza a cambiar
    lo que se cobra, así que por default se conserva el de la partida y tomar el
    de la lista es una elección explícita de quien cruza.
    """
    rem = get_or_404(db, Remision, rem_id)
    if not ctx.cliente_permitido(rem.cliente_facturacion_id):
        raise HTTPException(status_code=404, detail="Remisión no encontrada")
    _exigir_editable(db, rem)
    # Repuntar una partida a otro producto desincroniza el archivo igual que
    # reescribirla: aquí no hay excepción de acuse que valga.
    _exigir_no_exportada(rem)

    ln = next((x for x in rem.lineas if x.id == linea_id), None)
    if ln is None:
        raise HTTPException(status_code=404, detail="Esa partida no es de esta remisión")
    _ensure_productos(db, [payload.producto_id])
    prod = db.query(Producto).filter(Producto.id == payload.producto_id).one()
    presentaciones = prod.presentaciones or {}

    if payload.presentacion:
        presentacion = payload.presentacion.strip().upper()
        if presentacion not in presentaciones:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{prod.nombre} no se vende por {presentacion}; "
                    "da de alta esa presentación en el producto antes de cruzar"
                ),
            )
    else:
        # Sin presentación explícita se conserva la de la partida, y solo si el
        # producto nuevo la tiene: heredar una que no existe dejaría el factor
        # en 1 y el inventario descontaría manojos como si fueran kilos.
        presentacion = (
            ln.presentacion
            if ln.presentacion in presentaciones
            else (prod.presentacion_default or prod.unidad_base)
        )

    cantidad = (
        payload.cantidad_solicitada
        if payload.cantidad_solicitada is not None
        else ln.cantidad_solicitada
    )
    if Decimal(str(cantidad or 0)) <= 0:
        raise HTTPException(
            status_code=422,
            detail=(
                f"La partida {ln.numero_linea} quedó en cero (devuelta por completo): "
                "captura la cantidad con la que va el producto nuevo"
            ),
        )

    cambia = (
        payload.producto_id != ln.producto_id
        or presentacion != ln.presentacion
        or Decimal(str(cantidad)) != Decimal(str(ln.cantidad_solicitada))
        or payload.precio == "lista"
    )
    texto_alias = (payload.aprender_texto or "").strip()
    if not cambia and not texto_alias:
        raise HTTPException(
            status_code=409,
            detail="La partida ya va a ese producto con esa presentación y cantidad",
        )

    # Confirmada + cambio real de inventario: se libera la reserva de TODA la
    # remisión y se vuelve a reservar con la partida ya cruzada (el mismo
    # camino que la reedición — la reserva vive por línea y por lote).
    era_confirmada = rem.estado == "CONFIRMADA"
    inv_cambio = (
        payload.producto_id != ln.producto_id
        or presentacion != ln.presentacion
        or Decimal(str(cantidad)) != Decimal(str(ln.cantidad_solicitada))
    )
    if era_confirmada and inv_cambio:
        _liberar_reservas(
            db, ctx, rem,
            motivo=f"Cruce de partida en remisión {rem.folio_interno} (libera reserva previa)",
        )
        db.flush()

    antes = db.query(Producto.nombre).filter(Producto.id == ln.producto_id).scalar()
    cambia_producto = payload.producto_id != ln.producto_id
    ln.producto_id = payload.producto_id
    ln.presentacion = presentacion
    ln.cantidad_solicitada = cantidad
    if payload.precio == "lista":
        precio = _precios_de_lineas(db, rem, [{
            "producto_id": ln.producto_id,
            "presentacion": ln.presentacion,
            "cantidad_solicitada": ln.cantidad_solicitada,
            "precio_unitario": None,
        }])[0]
        if precio is None or precio <= 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{prod.nombre} no tiene precio en la lista de este cliente; "
                    "deja el precio de la partida o captúralo en la lista"
                ),
            )
        ln.precio_unitario = precio
    ln.importe = ln.cantidad_solicitada * ln.precio_unitario
    prod_fiscal, esq = _fiscal_por_producto(db, [ln.producto_id]).get(ln.producto_id, (None, None))
    calc = calcular_linea_producto(prod_fiscal, esq, ln.importe, ln.cantidad_solicitada)
    ln.iva_importe = calc["iva_importe"]
    ln.ieps_importe = calc["ieps_importe"]
    # El rastro se queda en la propia partida: quien la mire después tiene que
    # poder ver que el producto no es el que trajo el documento.
    if cambia_producto and antes:
        rastro = f"Cruzada a mano: venía a «{antes}»"
        ln.notas = f"{ln.notas.strip()} · {rastro}" if (ln.notas or "").strip() else rastro

    rem.subtotal = sum((x.importe or _ZERO for x in rem.lineas), _ZERO)
    rem.iva = sum((x.iva_importe or _ZERO for x in rem.lineas), _ZERO)
    rem.ieps = sum((x.ieps_importe or _ZERO for x in rem.lineas), _ZERO)
    rem.total = rem.subtotal - (rem.descuento or _ZERO) + rem.iva + rem.ieps

    if era_confirmada and inv_cambio:
        db.flush()
        db.refresh(rem)
        reservar_stock_remision(db, ctx, rem, permitir_negativos=payload.permitir_negativos)

    # Lo aprendido: por default SOLO para este cliente. El motivo del cruce
    # suele ser el catálogo de ESE cliente en SAE, no una verdad del vocabulario
    # de la casa —un global mal puesto ya mandó 24 remisiones equivocadas—, así
    # que «global» pasa por el helper de alcance, que jamás reapunta un global
    # ajeno: si el texto ya significa otro producto, aterriza en el cliente.
    if texto_alias:
        if payload.aprender_alcance == "global":
            aprender_alias_con_alcance(
                db, ctx.tenant_id, texto_alias, payload.producto_id,
                cliente_id=rem.cliente_facturacion_id, sucursal_id=rem.sucursal_id,
                origen="MANUAL", user_id=ctx.user_id,
            )
        else:
            aprender_alias(
                db, ctx.tenant_id, texto_alias, payload.producto_id,
                cliente_id=rem.cliente_facturacion_id,
                sucursal_id=rem.sucursal_id if payload.aprender_alcance == "plaza" else None,
                origen="MANUAL", user_id=ctx.user_id,
            )

    # El cruce puede completar la llave con el espejo (el producto correcto hace
    # que el importe cuadre con la factura de SAE), igual que la edición.
    ligar_remision_con_espejo(db, rem)
    rem.updated_by = ctx.user_id
    db.flush()
    db.refresh(rem)
    return _decorar_detalle(db, ctx, rem)


def exigir_precios(rem: Remision) -> None:
    """Una remisión con líneas en $0 no sale ni se factura.

    Capturar sin precio es legítimo mientras es borrador (el precio puede no
    estar negociado todavía). Mover inventario o timbrar con esa línea en cero
    es regalar mercancía y emitir un CFDI mal, así que el candado vive aquí, en
    el paso que compromete, y no en el que captura.
    """
    sin_precio = [ln for ln in rem.lineas if (ln.precio_unitario or 0) <= 0]
    if not sin_precio:
        return
    detalle = ", ".join(str(ln.numero_linea) for ln in sorted(sin_precio, key=lambda x: x.numero_linea))
    plural = "las líneas" if len(sin_precio) > 1 else "la línea"
    raise HTTPException(
        status_code=422,
        detail=(
            f"La remisión {rem.folio_interno} tiene {plural} {detalle} sin precio. "
            "Captúralo, o agrega el producto a la lista del cliente, antes de confirmar o facturar."
        ),
    )


def reservar_stock_remision(
    db: Session,
    ctx: AuthContext,
    rem: Remision,
    *,
    permitir_negativos: bool = False,
    pesos: dict | None = None,
) -> None:
    """Reserva inventario para una remisión BORRADOR y la deja CONFIRMADA.

    Cada línea trae presentación + cantidad; se reserva el equivalente en unidad
    base (`disponible → reservada`), se estampa la cantidad reservada en la línea
    (`cantidad_surtida`/`lote_id`) para que la cancelación libere exactamente lo
    mismo, y se registra un movimiento SALIDA_REMISION por línea. Lanza 422 si
    falta existencia y no se autorizó sobregiro. Compartida por el endpoint de
    confirmar y por facturar-desde-remisiones (auto-confirma el borrador).
    """
    if rem.almacen_id is None:
        raise HTTPException(status_code=422, detail="La remisión requiere un almacén para reservar inventario")
    exigir_precios(rem)
    pesos = pesos or {}
    prod_ids = {ln.producto_id for ln in rem.lineas}
    productos = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(prod_ids)).all()}

    for ln in rem.lineas:
        factor = presentacion_factor(productos.get(ln.producto_id), ln.presentacion)
        real = pesos.get(ln.id)
        base_qty = real if real is not None else (ln.cantidad_solicitada * factor)
        # Con sobregiro autorizado creamos el lote por defecto si no existe, para
        # poder reservar contra él (la disponible quedará en negativo).
        lote = resolve_lote(
            db, ctx.tenant_id, ln.producto_id, rem.almacen_id,
            numero_lote=None, create=permitir_negativos,
        )
        if lote is None or (not permitir_negativos and lote.cantidad_disponible < base_qty):
            raise HTTPException(
                status_code=422,
                detail=f"Existencia insuficiente para la línea {ln.numero_linea}",
            )
        # Decisión de negocio 2026-07-29: confirmar = el camión salió → salida
        # directa de disponible, SIN cubeta "reservada" (siempre queda en 0).
        # El stamp lote_id/cantidad_surtida en la línea permite restituir
        # exactamente lo mismo si la remisión se cancela.
        lote.cantidad_disponible = lote.cantidad_disponible - base_qty
        ln.lote_id = lote.id
        ln.cantidad_surtida = base_qty
        db.add(build_movimiento(
            ctx.tenant_id, ctx.user_id, lote, "SALIDA_REMISION", -base_qty,
            ref_tipo="REMISION", ref_id=rem.id, motivo=f"Reserva remisión {rem.folio_interno}",
        ))

    rem.estado = "CONFIRMADA"
    rem.updated_by = ctx.user_id


def _exigir_revisada(rem: Remision, accion: str) -> None:
    """Una remisión que llegó sin revisar no avanza sola.

    Confirmar reserva inventario, facturar la manda al SAT y el export la manda
    a SAE: las tres cosas dan por buenas unas unidades y unos precios que nadie
    miró. Poder pasar la orden sin revisar solo es defendible con este freno
    puesto — si no, «sin revisar» sería un atajo hasta el timbrado.
    """
    if rem.revision_pendiente:
        raise HTTPException(
            status_code=409,
            detail=(
                f"La remisión {rem.folio_interno} llegó sin revisar; "
                f"revísala y dala por revisada antes de {accion}"
            ),
        )


@router.post("/{rem_id}/confirmar", response_model=RemisionDetailOut)
def confirmar_remision(
    rem_id: UUID,
    payload: ConfirmarRemisionIn | None = Body(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    rem = get_or_404(db, Remision, rem_id)
    if rem.estado not in ("BORRADOR", "RESERVADO"):
        raise HTTPException(
            status_code=409,
            detail=f"Solo se confirma desde BORRADOR o RESERVADO (actual: {rem.estado})",
        )
    _exigir_revisada(rem, "confirmar")

    # Optional per-line real weights (catch-weight); override the estimate.
    pesos = {p.linea_id: p.cantidad_base for p in (payload.pesos or [])} if payload else {}
    # Sobregiro autorizado: confirma sin existencia suficiente (inventario negativo).
    permitir_negativos = bool(payload.permitir_negativos) if payload else False

    reservar_stock_remision(db, ctx, rem, permitir_negativos=permitir_negativos, pesos=pesos)
    db.flush()
    db.refresh(rem)
    return rem


@router.post("/{rem_id}/liberar-pedido", response_model=RemisionDetailOut)
def liberar_pedido(
    rem_id: UUID,
    payload: LiberarPedidoIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """La llave del candado de PEDIDO (decisión del dueño, 21-sep-2026).

    Una remisión que salió en un archivo de export se congela. Para el export de
    FACTURA la llave ya existía: el espejo limpia `export_sae_at` cuando SAE
    cancela esa factura. Para el de PEDIDO no la limpiaba nadie — cinco
    remisiones quedaron congeladas sin salida — y un candado sin llave es una
    trampa. Esta es la llave: una PERSONA declara que aquel archivo no se
    importó (o que el pedido se canceló allá), deja el motivo por escrito, y la
    remisión vuelve a ser editable.

    Solo personas: una conexión no tiene con qué saber qué pasó con un archivo
    dentro de Aspel. Es el mismo reparto que el candado de la impresión.
    """
    rem = get_or_404(db, Remision, rem_id)
    if ctx.conexion_id is not None:
        raise HTTPException(
            status_code=403,
            detail="Liberar del pedido lo hace una persona: hay que saber qué "
                   "pasó con el archivo en Aspel, y una conexión no puede saberlo",
        )
    if rem.export_pedido_at is None:
        raise HTTPException(status_code=409, detail="La remisión no está congelada por un pedido")
    if rem.export_sae_at is not None:
        # El candado de FACTURA tiene su propia llave (cancelar en SAE) y abrirlo
        # por aquí dejaría un CFDI exportado con la remisión editable debajo.
        raise HTTPException(
            status_code=409,
            detail="También salió en el masivo de FACTURA: esa se libera cancelando en SAE",
        )

    marca = f"{rem.export_pedido_at:%d/%m/%Y %H:%M}"
    folio = rem.export_pedido_folio or "s/folio"
    rem.export_pedido_at = None
    rem.export_pedido_folio = None
    # El rastro va en las notas del documento, que es donde el equipo lee: quién
    # la liberó, de qué archivo venía y por qué. No se borra historia: se anota.
    sello = (f"[{datetime.now(timezone.utc):%d/%m/%Y %H:%M} UTC] Liberada del pedido "
             f"{folio} (exportado {marca}): {payload.motivo.strip()}")
    rem.notas = f"{rem.notas}\n{sello}" if rem.notas else sello
    rem.updated_by = ctx.user_id
    db.flush()
    db.refresh(rem)
    return rem


@router.post("/{rem_id}/cancelar", response_model=RemisionDetailOut)
def cancelar_remision(
    rem_id: UUID,
    payload: Optional[CancelarRemisionIn] = Body(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Cancela la remisión y deja dicho por qué.

    El motivo viaja AQUÍ y no en un PATCH previo a propósito: editar una
    remisión que ya salió en un masivo devuelve 409 (`_exigir_no_exportada`),
    así que el camino de dos llamadas fallaba justo donde la nota más importa —
    y entre las dos quedaba una ventana con la nota escrita y la cancelación
    no."""
    rem = get_or_404(db, Remision, rem_id)
    if rem.estado == "CANCELADA":
        raise HTTPException(status_code=409, detail="La remisión ya está cancelada")
    if rem.estado == "FACTURADA":
        raise HTTPException(
            status_code=409,
            detail="La remisión está facturada; cancela su factura primero (eso libera el inventario y permite refacturar)",
        )

    if rem.estado == "CONFIRMADA":
        _liberar_reservas(db, ctx, rem, motivo=f"Cancelación remisión {rem.folio_interno}")

    motivo = ((payload.motivo if payload else None) or "").strip()
    if motivo:
        sello = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        nota = f"[{sello}] Cancelada: {motivo}"
        rem.notas = f"{rem.notas} · {nota}" if (rem.notas or "").strip() else nota
    rem.estado = "CANCELADA"
    rem.updated_by = ctx.user_id
    db.flush()
    db.refresh(rem)
    return rem


@router.post("/importar-preview")
def importar_preview(
    archivo: UploadFile = File(...),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Importación masiva (SAE o Master Ordenes): parsea el Excel, agrupa por
    FOLIO (una remisión por folio del archivo) y cruza cliente (código, RFC o
    nombre) y productos (CLAVE/SKU exacto; si no, candidatos del cruce). NO crea
    nada: la UI muestra el preview, el usuario resuelve lo no cruzado y crea con
    POST /remisiones."""
    _MAX = 5 * 1024 * 1024
    data = archivo.file.read(_MAX + 1)
    if len(data) > _MAX:
        raise HTTPException(status_code=422, detail="El archivo no debe exceder 5 MB")
    try:
        grupos = agrupar_por_folio(parsear_excel(data, archivo.filename or "archivo"))
    except ImportError_ as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if len(grupos) > 200:
        raise HTTPException(status_code=422, detail="Máximo 200 remisiones por archivo")

    # Cruce de clientes: código (SAE, exacto y sin ceros a la izquierda), RFC o
    # nombre normalizado (Master Ordenes trae "RFC Cliente"/"Nombre Cliente").
    clientes = db.query(Cliente).filter(Cliente.deleted_at.is_(None)).all()
    por_codigo: dict[str, Cliente] = {}
    por_rfc: dict[str, Cliente] = {}
    por_nombre: dict[str, Cliente] = {}
    for c in clientes:
        cod = (c.codigo or "").strip().upper()
        if cod:
            por_codigo.setdefault(cod, c)
            por_codigo.setdefault(normalizar_folio(cod).upper(), c)
        rfc = (c.rfc or "").strip().upper()
        if rfc:
            por_rfc.setdefault(rfc, c)
        for nombre in (c.legal_name, getattr(c, "nombre_comercial", None)):
            nom = normalizar_nombre(nombre)
            if nom:
                por_nombre.setdefault(nom, c)

    # Cruce de productos por CLAVE = SKU (exacto); si no, candidatos del cruce.
    # Alias y catálogo normalizado se cargan UNA vez: sin esto, cada línea sin
    # cruce hace un SELECT de alias y renormaliza el catálogo entero (1,125
    # líneas del master = minutos contra la base en la nube).
    catalogo = productos_activos(db, ctx.tenant_id)
    por_sku = {p.sku.strip().upper(): p for p in catalogo if p.sku}
    aliases = alias_del_tenant(db, ctx.tenant_id)
    norms = normalizar_catalogo(catalogo)
    cache_candidatos: dict[tuple, list[dict]] = {}
    por_id = {p.id: p for p in catalogo}
    # Código del cliente → producto: el master trae la CLAVE del cliente, no el
    # SKU propio ("AJO -FRUT-017" vs "00000282"). Es el cruce EXACTO y va antes
    # que cualquier parecido de texto.
    #   1) (cliente, código) — lo que ese cliente pidió con esa clave.
    #   2) (código) a secas — la misma clave usada con otro cliente; el esquema
    #      de claves es uno solo, así que sirve mientras no apunte a productos
    #      distintos (ahí se descarta y decide el usuario).
    por_codigo_cliente: dict[tuple, Producto] = {}
    por_codigo: dict[str, Optional[Producto]] = {}
    for pc in db.query(ProductoCliente).all():
        cod = _norm_codigo(pc.codigo_cliente)
        prod_pc = por_id.get(pc.producto_id)
        if not cod or prod_pc is None:
            continue
        por_codigo_cliente.setdefault((pc.cliente_id, cod), prod_pc)
        if cod in por_codigo:
            if por_codigo[cod] is not None and por_codigo[cod].id != prod_pc.id:
                por_codigo[cod] = None      # la misma clave en dos productos: ambigua
        else:
            por_codigo[cod] = prod_pc

    sin_cliente = 0
    sin_producto = 0
    out = []
    for g in grupos:
        cod = str(g["cliente"]).strip().upper()
        rfc = str(g.get("cliente_rfc") or "").strip().upper()
        cli = (
            por_rfc.get(rfc)
            or por_codigo.get(cod)
            or por_codigo.get(normalizar_folio(cod).upper())
            or por_nombre.get(normalizar_nombre(g["cliente"]))
        )
        if cli is None:
            sin_cliente += 1
        lineas = []
        for ln in g["lineas"]:
            clave = ln["clave"].strip().upper()
            cod = _norm_codigo(ln["clave"])
            prod = por_sku.get(clave)
            cruce = "sku" if prod else None
            if prod is None and cli is not None:
                prod = por_codigo_cliente.get((cli.id, cod))
                cruce = "cliente" if prod else None
            if prod is None:
                prod = por_codigo.get(cod)
                cruce = "codigo" if prod else None
            candidatos = []
            if prod is None:
                sin_producto += 1
                # Candidatos por CLAVE y, si el archivo la trae (Master Ordenes),
                # también por DESCRIPCION: el nombre cruza mejor que la clave ajena.
                # El master repite las mismas claves en las 64 órdenes → se cachea
                # el resultado por (clave, descripción).
                # La descripción es el texto que describe la mercancía; la
                # clave es el código del cliente y solo sirve si no hay otra cosa.
                texto_ref = ln.get("descripcion") or ln["clave"]
                key = (ln["clave"], ln.get("descripcion"))
                candidatos = cache_candidatos.get(key)
                if candidatos is None:
                    vistos: set = set()
                    candidatos = []
                    for texto in key:
                        if not texto:
                            continue
                        for c in buscar(db, ctx.tenant_id, texto, limit=3, prods=catalogo,
                                        aliases=aliases, norms=norms):
                            if c.producto_id in vistos:
                                continue
                            vistos.add(c.producto_id)
                            candidatos.append({
                                "producto_id": c.producto_id, "sku": c.sku,
                                "nombre": c.nombre, "score": c.score,
                                "origen": c.origen,
                                # El score del catálogo premia al SUBCONJUNTO
                                # ("TOMATE" saca 100 contra "TOMATE SERRANO"),
                                # necesario porque los nombres vienen cortados a
                                # 30 caracteres. `parecido` compara los textos
                                # COMPLETOS y desempata entre esos empates.
                                "parecido": int(fuzz.ratio(normalizar(texto_ref), normalizar(c.nombre))),
                            })
                    candidatos.sort(key=lambda c: (c["score"], c["parecido"]), reverse=True)
                    candidatos = cache_candidatos[key] = candidatos[:5]
                # Se cruza solo cuando el mejor candidato es inequívoco: score
                # máximo del catálogo, parecido real con el texto completo, y
                # ventaja clara sobre los que empatan en score. Lo dudoso queda
                # en ámbar para que lo resuelva el usuario.
                mejor = candidatos[0] if candidatos else None
                empatados = [c for c in candidatos[1:] if mejor and c["score"] == mejor["score"]]
                if (
                    mejor is not None
                    and mejor["score"] == 100
                    and mejor["parecido"] >= 60
                    and all(mejor["parecido"] - c["parecido"] >= 10 for c in empatados)
                ):
                    prod = por_id.get(mejor["producto_id"])
                    if prod is not None:
                        cruce = "descripcion"
                        sin_producto -= 1
            lineas.append({
                "clave": ln["clave"],
                "cruce": cruce,
                "descripcion": ln.get("descripcion"),
                "unidad": ln.get("unidad"),
                "cantidad": ln["cantidad"],
                "precio": ln["precio"],
                "producto_id": prod.id if prod else None,
                "producto_nombre": prod.nombre if prod else None,
                "presentacion": (prod.presentacion_default if prod else None),
                "candidatos": candidatos,
            })
        out.append({
            "folio_ref": g["folio_ref"],
            "fecha": g["fecha"],
            "su_pedido": g["su_pedido"],
            "observaciones": g["observaciones"],
            "cliente_codigo": g["cliente"],
            "cliente_rfc": g.get("cliente_rfc"),
            "requisicion": g.get("requisicion"),
            "entregar_bodega": g.get("entregar_bodega"),
            "cliente_id": cli.id if cli else None,
            "cliente_nombre": cli.legal_name if cli else None,
            "lineas": lineas,
        })
    return {
        "grupos": out,
        "clientes_sin_cruce": sin_cliente,
        "productos_sin_cruce": sin_producto,
    }


class DevolucionLineaIn(BaseModel):
    linea_id: UUID
    # En unidades de la PRESENTACIÓN de la línea (igual que se capturó).
    cantidad: Decimal = PydField(gt=0)


class DevolucionIn(BaseModel):
    lineas: list[DevolucionLineaIn] = PydField(min_length=1, max_length=200)
    motivo: Optional[str] = PydField(default=None, max_length=500)


@router.post("/{rem_id}/devolucion", response_model=RemisionDetailOut)
def devolucion_remision(
    rem_id: UUID,
    payload: DevolucionIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Devolución parcial o total de una remisión CONFIRMADA (el camión ya salió).

    Decisión 2026-07-29: la devolución AJUSTA la remisión a lo neto entregado —
    reduce cantidades/importes/impuestos de las líneas (la factura posterior sale
    por lo neto), regresa el inventario a disponible (ENTRADA_DEVOLUCION) y deja
    el rastro en `devoluciones`.
    """
    rem = get_or_404(db, Remision, rem_id, for_update=True)
    if rem.estado != "CONFIRMADA":
        detalle = {
            "BORRADOR": "La remisión está en borrador: aún no sale mercancía que devolver",
            "RESERVADO": "La remisión está reservada: aún no sale mercancía que devolver",
            "FACTURADA": "La remisión ya está facturada; cancela la factura primero",
            "CANCELADA": "La remisión está cancelada",
        }.get(rem.estado, f"No se puede devolver en estado {rem.estado}")
        raise HTTPException(status_code=409, detail=detalle)

    por_id = {l.id: l for l in rem.lineas}
    fiscal = _fiscal_por_producto(db, [l.producto_id for l in rem.lineas])

    dev = Devolucion(
        tenant_id=ctx.tenant_id, remision_id=rem.id,
        motivo=(payload.motivo or "").strip() or None, created_by=ctx.user_id,
    )
    db.add(dev)
    db.flush()

    for item in payload.lineas:
        ln = por_id.get(item.linea_id)
        if ln is None:
            raise HTTPException(status_code=422, detail="Una línea no pertenece a la remisión")
        if item.cantidad > ln.cantidad_solicitada:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"La línea {ln.numero_linea} tiene {ln.cantidad_solicitada} y se "
                    f"intentan devolver {item.cantidad}"
                ),
            )
        prod, esq = fiscal.get(ln.producto_id, (None, None))
        factor = presentacion_factor(prod, ln.presentacion)
        base = item.cantidad * factor
        if ln.cantidad_surtida is not None:
            # Catch-weight: nunca regresar al inventario más de lo que salió.
            base = min(base, Decimal(ln.cantidad_surtida))

        # Inventario: regresa a disponible en el lote del que salió.
        lote = None
        if ln.lote_id is not None:
            lote = (
                db.query(LoteInventario)
                .filter(LoteInventario.id == ln.lote_id)
                .with_for_update()
                .one_or_none()
            )
        if lote is None:
            lote = resolve_lote(
                db, ctx.tenant_id, ln.producto_id, rem.almacen_id, numero_lote=None, create=True
            )
        lote.cantidad_disponible = lote.cantidad_disponible + base
        db.add(build_movimiento(
            ctx.tenant_id, ctx.user_id, lote, "ENTRADA_DEVOLUCION", base,
            ref_tipo="REMISION", ref_id=rem.id,
            motivo=f"Devolución remisión {rem.folio_interno}",
        ))

        # La línea queda por lo NETO entregado (misma regla fiscal del alta).
        ln.cantidad_solicitada = ln.cantidad_solicitada - item.cantidad
        if ln.cantidad_surtida is not None:
            ln.cantidad_surtida = max(_ZERO, Decimal(ln.cantidad_surtida) - base)
        ln.importe = ln.cantidad_solicitada * ln.precio_unitario
        calc = calcular_linea_producto(prod, esq, ln.importe, ln.cantidad_solicitada)
        ln.iva_importe = calc["iva_importe"]
        ln.ieps_importe = calc["ieps_importe"]

        db.add(LineaDevolucion(
            tenant_id=ctx.tenant_id, devolucion_id=dev.id, linea_remision_id=ln.id,
            producto_id=ln.producto_id, presentacion=ln.presentacion,
            cantidad=item.cantidad, cantidad_base=base,
        ))

    rem.subtotal = sum((l.importe or _ZERO for l in rem.lineas), _ZERO)
    rem.iva = sum((l.iva_importe or _ZERO for l in rem.lineas), _ZERO)
    rem.ieps = sum((l.ieps_importe or _ZERO for l in rem.lineas), _ZERO)
    rem.total = rem.subtotal - (rem.descuento or _ZERO) + rem.iva + rem.ieps
    rem.updated_by = ctx.user_id
    db.flush()
    db.refresh(rem)
    return rem


class PreviewLineaIn(BaseModel):
    producto_id: UUID
    cantidad: Decimal = PydField(gt=0)
    precio_unitario: Decimal = PydField(ge=0)
    presentacion: Optional[str] = PydField(default=None, max_length=20)


class PreviewTotalesIn(BaseModel):
    lineas: list[PreviewLineaIn] = PydField(min_length=1, max_length=500)
    descuento: Decimal = PydField(default=Decimal("0"), ge=0)


@router.post("/preview-totales")
def preview_totales(
    payload: PreviewTotalesIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Totales calculados por el SERVIDOR para el preview del alta (remisión o
    factura directa): el frontend nunca deriva impuestos por su cuenta — regla
    "el backend calcula todo" (2026-07-29). Mismo cerebro que el documento real."""
    if ctx.cliente_scope:
        raise HTTPException(status_code=403, detail="Tu usuario no captura documentos")
    fiscal = _fiscal_por_producto(db, [ln.producto_id for ln in payload.lineas])
    subtotal = _ZERO
    iva = _ZERO
    ieps = _ZERO
    por_linea = []
    for ln in payload.lineas:
        importe = ln.cantidad * ln.precio_unitario
        subtotal += importe
        prod, esq = fiscal.get(ln.producto_id, (None, None))
        calc = calcular_linea_producto(prod, esq, importe, ln.cantidad)
        iva += calc["iva_importe"]
        ieps += calc["ieps_importe"]
        por_linea.append({
            "importe": importe,
            "iva_importe": calc["iva_importe"],
            "ieps_importe": calc["ieps_importe"],
        })
    return {
        "subtotal": subtotal,
        "descuento": payload.descuento,
        "iva": iva,
        "ieps": ieps,
        "total": subtotal - payload.descuento + iva + ieps,
        "lineas": por_linea,      # mismo orden que el payload
    }


class EnviarRemisionIn(BaseModel):
    to: Optional[str] = PydField(default=None, max_length=1000)
    mensaje: Optional[str] = PydField(default=None, max_length=5000)


class EnviarRemisionesLoteIn(BaseModel):
    """Un solo correo con varias remisiones (todas del mismo cliente)."""
    ids: list[UUID] = PydField(min_length=1, max_length=100)
    to: Optional[str] = PydField(default=None, max_length=1000)
    mensaje: Optional[str] = PydField(default=None, max_length=5000)


def _fmt_money(value: Decimal) -> str:
    return f"${value:,.2f}"


def _validar_destinatarios(destinatarios: list[str]) -> list[str]:
    """Cada token debe ser un correo válido: un valor arbitrario llegaría hasta
    el header `To` del SMTP (502 críptico en el mejor caso, inyección de headers
    en el peor)."""
    limpios: list[str] = []
    for d in destinatarios:
        try:
            limpios.append(validate_email(d, check_deliverability=False).normalized)
        except EmailNotValidError:
            raise HTTPException(status_code=422, detail=f"Correo inválido: {d}")
    return limpios


def _build_remision_html(rem: Remision, cliente_nombre: str, lineas: list) -> str:
    filas = []
    total = _ZERO
    for ln in lineas:
        importe = ln.importe or _ZERO
        total += importe
        filas.append(
            "<tr>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{html_mod.escape(ln.producto_nombre or '')}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{html_mod.escape(str(ln.presentacion or ''))}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{ln.cantidad_solicitada:,.2f}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_fmt_money(ln.precio_unitario or _ZERO)}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_fmt_money(importe)}</td>"
            "</tr>"
        )
    return (
        "<div style='font-family:Arial,Helvetica,sans-serif;color:#222'>"
        f"<h2 style='margin:0 0 4px'>Remisión {html_mod.escape(rem.folio_interno or '')}</h2>"
        f"<p style='margin:0 0 2px'><strong>Cliente:</strong> {html_mod.escape(cliente_nombre)}</p>"
        f"<p style='margin:0 0 16px'><strong>Fecha:</strong> {rem.fecha_remision}</p>"
        "<table style='border-collapse:collapse;width:100%;font-size:14px'>"
        "<thead><tr style='background:#f5f5f5'>"
        "<th style='padding:6px 10px;text-align:left'>Producto</th>"
        "<th style='padding:6px 10px;text-align:left'>Presentación</th>"
        "<th style='padding:6px 10px;text-align:right'>Cantidad</th>"
        "<th style='padding:6px 10px;text-align:right'>Precio</th>"
        "<th style='padding:6px 10px;text-align:right'>Importe</th>"
        "</tr></thead><tbody>"
        + "".join(filas)
        + "</tbody></table>"
        + (
            f"<p style='margin:12px 0 0;text-align:right;font-size:13px;color:#555'>"
            f"Subtotal: {_fmt_money(rem.subtotal or total)}"
            + (f" · IVA: {_fmt_money(rem.iva)}" if rem.iva and Decimal(rem.iva) > 0 else "")
            + (f" · IEPS: {_fmt_money(rem.ieps)}" if rem.ieps and Decimal(rem.ieps) > 0 else "")
            + "</p>"
        )
        + f"<p style='margin:4px 0 0;text-align:right;font-size:16px'>"
        f"<strong>Total: {_fmt_money(rem.total if rem.total is not None else total)}</strong></p>"
        "</div>"
    )


def _build_remisiones_lote_html(
    rems: list, cliente_nombre: str, mensaje: Optional[str], emisor_nombre: str
) -> str:
    """Cuerpo del correo cuando se envían VARIAS remisiones de un cliente en un
    solo correo: saludo, mensaje opcional, un resumen (folio/fecha/total) y el
    total general. El detalle de cada remisión va en su PDF adjunto."""
    filas = []
    total_general = _ZERO
    for rem in rems:
        # Total oficial del documento (con impuestos) — el mismo de lista/PDF.
        total_rem = rem.total if rem.total is not None else sum(
            (ln.importe or _ZERO for ln in rem.lineas), _ZERO
        )
        total_general += total_rem
        filas.append(
            "<tr>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{html_mod.escape(rem.folio_interno or '')}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{rem.fecha_remision}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_fmt_money(total_rem)}</td>"
            "</tr>"
        )
    n = len(rems)
    etiqueta = "remisión" if n == 1 else "remisiones"
    mensaje_html = (
        "<div style='background:#f5f7ff;border-left:3px solid #4f6bed;"
        "padding:10px 14px;margin:0 0 16px;border-radius:4px;white-space:pre-line'>"
        f"{html_mod.escape(mensaje)}</div>"
        if mensaje else ""
    )
    return (
        "<div style='font-family:Arial,Helvetica,sans-serif;color:#222;max-width:640px'>"
        f"<h2 style='margin:0 0 4px'>{html_mod.escape(emisor_nombre)}</h2>"
        f"<p style='margin:0 0 16px;color:#555'>Estimado(a) <strong>{html_mod.escape(cliente_nombre)}</strong>:</p>"
        + mensaje_html
        + f"<p style='margin:0 0 16px'>Le compartimos {n} {etiqueta}. "
        "El detalle completo de cada una se encuentra en el PDF adjunto correspondiente.</p>"
        "<table style='border-collapse:collapse;width:100%;font-size:14px'>"
        "<thead><tr style='background:#f5f5f5'>"
        "<th style='padding:6px 10px;text-align:left'>Remisión</th>"
        "<th style='padding:6px 10px;text-align:left'>Fecha</th>"
        "<th style='padding:6px 10px;text-align:right'>Total</th>"
        "</tr></thead><tbody>"
        + "".join(filas)
        + "</tbody></table>"
        f"<p style='margin:16px 0 0;text-align:right;font-size:16px'>"
        f"<strong>Total general: {_fmt_money(total_general)}</strong></p>"
        "<p style='margin:24px 0 0;color:#999;font-size:12px'>"
        "Correo enviado automáticamente por Facturador.</p>"
        "</div>"
    )


@router.post("/{rem_id}/enviar")
def enviar_remision(
    rem_id: UUID,
    payload: EnviarRemisionIn | None = Body(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    rem = get_or_404(db, Remision, rem_id)
    if rem.estado == "CANCELADA":
        raise HTTPException(status_code=409, detail="La remisión está cancelada; no se envía por correo")
    cliente = db.query(Cliente).filter(Cliente.id == rem.cliente_facturacion_id).one_or_none()

    # Destinatarios: los que vengan en el payload (uno o varios, coma/espacio) o,
    # en su defecto, los correos del cliente (`correos` array, o el `email` legado).
    destinatarios: list[str] = []
    if payload and payload.to:
        destinatarios = [c for c in payload.to.replace(",", " ").split() if c]
    if not destinatarios and cliente is not None:
        dom = cliente.domicilio_fiscal or {}
        correos = dom.get("correos")
        if isinstance(correos, list):
            destinatarios = [str(c).strip() for c in correos if str(c).strip()]
        elif dom.get("email"):
            destinatarios = [str(dom["email"]).strip()]
    if not destinatarios:
        raise HTTPException(status_code=422, detail="El cliente no tiene correo")
    destinatarios = _validar_destinatarios(destinatarios)

    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one_or_none()
    if not email_service.configured(tenant):
        raise HTTPException(status_code=503, detail="El correo no está configurado")

    # Nombres de producto COMO LOS CONOCE EL CLIENTE (catálogo del cliente, con
    # el interno de respaldo): el correo y su PDF los lee él, no el operador.
    names = _nombres_para_pdf(db, [rem])[rem.id]
    for ln in rem.lineas:
        ln.producto_nombre = names.get(ln.producto_id)

    cliente_nombre = cliente.legal_name if cliente else ""
    mensaje_html = f"<p>{html_mod.escape(payload.mensaje)}</p>" if (payload and payload.mensaje) else ""
    html = mensaje_html + _build_remision_html(rem, cliente_nombre, rem.lineas)

    # Se adjunta el PDF de la remisión (mismo diseño que la factura).
    folio = rem.folio_interno or ""
    pdf = build_remision_pdf(rem, tenant, cliente, names)
    attachments: list[tuple[str, bytes, str]] = [(f"{folio}.pdf", pdf, "application/pdf")]

    try:
        email_service.send_email(
            email_service.smtp_config(tenant),
            destinatarios,
            f"Remisión {folio}",
            html,
            attachments=attachments,
        )
    except Exception as exc:  # noqa: BLE001 — superficie del error al cliente
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "to": ", ".join(destinatarios)}


@router.post("/enviar-lote")
def enviar_remisiones_lote(
    payload: EnviarRemisionesLoteIn = Body(...),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Un SOLO correo con todas las remisiones indicadas (un PDF adjunto por
    remisión). Pensado para el envío masivo agrupado por cliente: cada cliente
    recibe un único correo con todas sus remisiones."""
    if not payload.ids:
        raise HTTPException(status_code=422, detail="Sin remisiones para enviar")
    if len(payload.ids) > 50:
        # Un PDF por remisión + SMTP con todo adjunto, con la conexión del
        # pool tomada mientras tanto — mismo tope que los PDF en lote.
        raise HTTPException(status_code=422, detail="Máximo 50 remisiones por correo")
    rems = (
        db.query(Remision)
        .filter(Remision.id.in_(payload.ids), Remision.deleted_at.is_(None))
        .order_by(Remision.folio_interno)
        .all()
    )
    if not rems:
        raise HTTPException(status_code=404, detail="No se encontraron remisiones")
    canceladas = [r.folio_interno for r in rems if r.estado == "CANCELADA"]
    if canceladas:
        raise HTTPException(
            status_code=409,
            detail="Remisiones canceladas en la selección: " + ", ".join(canceladas),
        )

    # El envío masivo agrupa por cliente, así que todas deben ser del mismo.
    cli_ids = {r.cliente_facturacion_id for r in rems}
    if len(cli_ids) > 1:
        raise HTTPException(status_code=422, detail="Las remisiones deben ser del mismo cliente")
    cliente = db.query(Cliente).filter(Cliente.id == next(iter(cli_ids))).one_or_none()

    # Destinatarios: los del payload (uno o varios, coma/espacio) o, en su
    # defecto, los correos del cliente (`correos` array, o el `email` legado).
    destinatarios: list[str] = []
    if payload.to:
        destinatarios = [c for c in payload.to.replace(",", " ").split() if c]
    if not destinatarios and cliente is not None:
        dom = cliente.domicilio_fiscal or {}
        correos = dom.get("correos")
        if isinstance(correos, list):
            destinatarios = [str(c).strip() for c in correos if str(c).strip()]
        elif dom.get("email"):
            destinatarios = [str(dom["email"]).strip()]
    if not destinatarios:
        raise HTTPException(status_code=422, detail="El cliente no tiene correo")
    destinatarios = _validar_destinatarios(destinatarios)

    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one_or_none()
    if not email_service.configured(tenant):
        raise HTTPException(status_code=503, detail="El correo no está configurado")

    # Nombres de producto de todas las remisiones (para el cuerpo y los PDFs),
    # como los conoce el cliente — es él quien recibe este correo.
    por_rem = _nombres_para_pdf(db, rems)
    for r in rems:
        for ln in r.lineas:
            ln.producto_nombre = por_rem[r.id].get(ln.producto_id)

    cliente_nombre = cliente.legal_name if cliente else ""
    emisor_nombre = (tenant.trade_name or tenant.legal_name) if tenant else "Facturador"
    mensaje = (payload.mensaje or "").strip() or None
    html = _build_remisiones_lote_html(rems, cliente_nombre, mensaje, emisor_nombre)

    # Un PDF adjunto por remisión.
    attachments: list[tuple[str, bytes, str]] = []
    for r in rems:
        pdf = build_remision_pdf(r, tenant, cliente, por_rem[r.id])
        folio = r.folio_interno or str(r.id)
        attachments.append((f"{folio}.pdf", pdf, "application/pdf"))

    n = len(rems)
    asunto = (
        f"Remisión {rems[0].folio_interno}" if n == 1
        else f"{n} remisiones — {emisor_nombre}"
    )
    try:
        email_service.send_email(
            email_service.smtp_config(tenant),
            destinatarios,
            asunto,
            html,
            attachments=attachments,
        )
    except Exception as exc:  # noqa: BLE001 — superficie del error al cliente
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "to": ", ".join(destinatarios), "remisiones": n}


@router.get("/{rem_id}/pdf")
def remision_pdf(
    rem_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """PDF de la remisión (mismo diseño que la factura, marcado NO FISCAL)."""
    rem = get_or_404(db, Remision, rem_id)
    if not ctx.cliente_permitido(rem.cliente_facturacion_id):
        raise HTTPException(status_code=404, detail="Remisión no encontrada")
    cliente = db.query(Cliente).filter(Cliente.id == rem.cliente_facturacion_id).one_or_none()
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    # El papel que firma el cliente trae SU clave y SU nombre del producto
    # (catálogo del cliente), con el interno de respaldo — igual que el CFDI.
    pdf = build_remision_pdf(rem, tenant, cliente, _nombres_para_pdf(db, [rem])[rem.id])
    _marcar_impresas(db, [rem])
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{rem.folio_interno}.pdf"'},
    )


@router.delete("/{rem_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_remision(
    rem_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("remision:eliminar")),
):
    rem = get_or_404(db, Remision, rem_id)
    if rem.estado == "FACTURADA":
        raise HTTPException(status_code=409, detail="La remisión está facturada; cancela su factura antes de eliminarla")
    if rem.estado == "CONFIRMADA":
        raise HTTPException(status_code=409, detail="Cancela la remisión antes de eliminarla (libera inventario)")
    rem.deleted_at = func.now()
    db.flush()
    return None


# ─── Export masivo para SAE (fase espejo de la migración) ────────────────────
# El Facturador genera el archivo que Aspel importa; el layout y las trampas
# (fechas MM/DD, relleno del folio, claves del cliente) viven en
# services/export_sae.py. El folio inicial lo CONFIRMA el operador contra SAE
# (regla D1 del plan): aquí solo se sugiere — tanto la serie de la factura
# como el consecutivo de pedidos de la empresa.

class ExportSaeIn(BaseModel):
    ids: list[UUID] = PydField(min_length=1)
    tipo: str = "FACTURA"                        # FACTURA | PEDIDO
    # {serie: folio_inicial} confirmados por el operador. En FACTURA, una
    # entrada por serie fiscal; en PEDIDO, una sola con la llave "PEDIDO"
    # (export_sae.SERIE_PEDIDO): el consecutivo de pedidos de la empresa SAE.
    folios: Optional[dict[str, int]] = None
    fecha: Optional[date] = None                 # default: hoy (MM/DD/YYYY en el archivo)


@router.post("/export-sae/preview")
def export_sae_preview(
    body: ExportSaeIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Valida el lote y sugiere folios SIN generar nada: la pantalla muestra
    esto antes de que el operador confirme. Los errores llegan completos (no
    solo el primero) porque el operador corrige todo de una pasada."""
    from ...services import export_sae as svc

    res, _docs = svc.preparar(db, ctx.tenant_id, body.ids, body.tipo)
    return {
        "ok": res.ok, "errores": res.errores, "avisos": res.avisos,
        "empresa": res.empresa, "series": res.series, "remisiones": res.remisiones,
        "fecha_ejemplo": res.fecha_ejemplo,
    }


@router.post("/export-sae")
def export_sae(
    body: ExportSaeIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Genera el .xls masivo (Excel 97-2004). NO estampa folios: los del
    archivo son la propuesta confirmada por el operador, y `factura_sae` lo
    pone el ESPEJO cuando la factura de verdad existe en SAE — un archivo que
    nunca se sube ya no deja folios fantasma en las remisiones."""
    from ...services import export_sae as svc

    res, contenido, nombre = svc.generar(
        db, ctx.tenant_id, body.ids, body.tipo,
        folios=body.folios, fecha=body.fecha,
    )
    if not res.ok or contenido is None:
        raise HTTPException(status_code=422, detail=" · ".join(res.errores) or "lote inválido")
    db.flush()
    return Response(
        content=contenido,
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
