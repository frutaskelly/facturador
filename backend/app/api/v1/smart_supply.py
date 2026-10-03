"""Panel de Smart Supply — OC, remisionado y facturado de UNA plaza, solo lectura.

Smart Supply (app.smartsupply.mx) mide la merma de cada bodega. Lo que se
pidió, lo que se remisionó y lo que se facturó viven aquí; Smart Supply lo lee
con la clave de SU cuenta (conexión `SMART_SUPPLY_PANEL`, permiso único
`abasto:leer`) y lo guarda en sus tablas. Nada de aquí escribe.

Todo responde dentro del alcance de la clave (services/smart_supply.py): las
series de factura, las de remisión y los perfiles por los que entran las OC de
esa plaza. Lo de otra plaza no existe para esa clave.

Reglas que valen para todo el router:

- Fechas de México y rangos acotados (`desde`/`hasta`, máx. 93 días).
- Paginación por LLAVE, nunca por offset: el espejo de SAE borra y recrea las
  líneas de una factura en cada reenvío, y con offset una página se salta o
  repite líneas cuando el conjunto cambia entre una llamada y la otra. Cada
  respuesta trae `siguiente`; la página que sigue se pide con `despues=`.
- Cantidades e importes como texto decimal; importes sin IVA.
- El `producto_id` del facturado es el que guardó la línea (el espejo lo pone
  por clave SAE desde #313). Aquí no se recalcula: dos copias de la regla
  terminan dando dos respuestas.
"""
from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Literal, Optional
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, aliased

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import (
    CategoriaProducto,
    Cliente,
    Conexion,
    Factura,
    LineaFactura,
    LineaRemision,
    OCRecibida,
    Producto,
    Proyecto,
    Remision,
    Serie,
    Sucursal,
    Tenant,
)
from ...models.oc_recibida import CANALES, ESTADOS as ESTADOS_OC
from ...schemas.smart_supply import (
    AlcancePanelOut,
    ConexionPanelBreveOut,
    EmpresaPanelOut,
    FacturaDeOCOut,
    FacturadoOut,
    OCLineaOut,
    OCPanelOut,
    Pagina,
    PlazaAlcanceOut,
    PresentacionOut,
    ProductoPanelOut,
    RemisionadoOut,
    RemisionDeOCOut,
)
from ...services import smart_supply as panel
from ...services.espejo_productos import norm_clave_sae
from ...services.inventario import claves_sae_por_presentacion
from ...services.mini_conta import remisiones_entregadas
from ...services.oc_cambios import NOTA_DOCUMENTO_REVERTIDO

router = APIRouter(prefix="/smart-supply", tags=["smart-supply"])

_LEER = "abasto:leer"
_ZONA = panel.ZONA
_MAX_DIAS = 93
_LIMIT = 1000
_MAX_LIMIT = 5000
_CENTAVO = Decimal("0.01")
_UNO = Decimal(1)


# ─── piezas comunes ──────────────────────────────────────────────────────────

def _rango(desde: date, hasta: date) -> None:
    if hasta < desde:
        raise HTTPException(status_code=422, detail="«hasta» no puede ser antes de «desde»")
    if (hasta - desde).days + 1 > _MAX_DIAS:
        raise HTTPException(status_code=422,
                            detail=f"El rango no puede pasar de {_MAX_DIAS} días")


def _cursor(*partes) -> str:
    crudo = json.dumps([str(p) for p in partes], separators=(",", ":"))
    return base64.urlsafe_b64encode(crudo.encode("utf-8")).decode("ascii").rstrip("=")


def _leer_cursor(texto: Optional[str], *tipos: Callable) -> Optional[tuple]:
    if not texto:
        return None
    try:
        crudo = base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))
        partes = json.loads(crudo)
        # `_cursor` solo escribe textos: un número o una lista adentro no salió
        # de aquí (y `UUID(123)` truena con AttributeError, no ValueError).
        if (not isinstance(partes, list) or len(partes) != len(tipos)
                or not all(isinstance(p, str) for p in partes)):
            raise ValueError
        return tuple(t(p) for t, p in zip(tipos, partes))
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
        raise HTTPException(
            status_code=422,
            detail="«despues» no es un cursor válido: usa el «siguiente» de la página anterior",
        )


def _ts(texto: str) -> datetime:
    t = datetime.fromisoformat(texto)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _pagina(filas: list, limit: int, llave: Callable) -> tuple[list, Optional[str]]:
    """Corta `limit + 1` filas en la página y el cursor de la siguiente (la
    llave de la ÚLTIMA fila entregada)."""
    if len(filas) <= limit:
        return filas, None
    filas = filas[:limit]
    return filas, _cursor(*llave(filas[-1]))


def _txt(v) -> Optional[str]:
    """Un número como texto, sin ceros de relleno («80», «25.5»): la columna
    trae 4 o 6 decimales y el factor multiplica la escala."""
    if v is None:
        return None
    d = v if isinstance(v, Decimal) else Decimal(str(v))
    if not d.is_finite():
        return None
    return format(d.normalize(), "f") if d != 0 else "0"


def _lista(texto: Optional[str], validos, que: str) -> Optional[list[str]]:
    pedidos = [x.strip().upper() for x in (texto or "").split(",") if x.strip()]
    if not pedidos:
        return None
    malos = sorted(set(pedidos) - set(validos))
    if malos:
        raise HTTPException(status_code=422, detail=f"{que} desconocido: {', '.join(malos)}")
    return pedidos


def _series(pedidas: Optional[str], permitidas: Optional[frozenset[str]]) -> Optional[frozenset[str]]:
    """Las series a leer: las pedidas (`?series=A,B`) o todas las de la clave.
    Pedir una que la clave no comparte es un 403 que lo dice, no un recorte
    silencioso: si el dueño le quitó una serie a la cuenta, Smart Supply tiene
    que enterarse."""
    lista = {x.strip() for x in (pedidas or "").split(",") if x.strip()}
    if not lista:
        return permitidas
    if permitidas is not None:
        fuera = sorted(lista - permitidas)
        if fuera:
            raise HTTPException(status_code=403,
                                detail=f"Esta conexión no comparte la serie {', '.join(fuera)}")
    return frozenset(lista)


def _entrada(presentaciones, nombre: str):
    """La entrada de `presentaciones` para ese nombre, sin importar mayúsculas."""
    for k, v in (presentaciones or {}).items():
        if str(k).strip().upper() == nombre:
            return v
    return None


def _factor(raw) -> Optional[Decimal]:
    val = raw.get("factor") if isinstance(raw, dict) else raw
    try:
        f = Decimal(str(val))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return f if f.is_finite() and f > 0 else None


def _kg(unidad: Optional[str], unidad_base: Optional[str], presentaciones,
        cantidad: Decimal) -> tuple[Optional[Decimal], Optional[Decimal], bool]:
    """(factor, kg, estimado) de una cantidad en `unidad`. Solo cuando se sabe:
    la unidad es KILO, o es una presentación con factor de un producto cuya
    unidad base es KILO. Si no, None: el peso no se inventa."""
    u = (unidad or "").strip().upper()
    if not u:
        return None, None, False
    if u in ("KILO", "KG"):
        return _UNO, cantidad, False
    if (unidad_base or "").strip().upper() != "KILO":
        return None, None, False
    raw = _entrada(presentaciones, u)
    f = _factor(raw) if raw is not None else None
    if f is None:
        return None, None, False
    estimado = bool(raw.get("estimado")) if isinstance(raw, dict) else False
    return f, cantidad * f, estimado


# ─── /alcance ────────────────────────────────────────────────────────────────

@router.get("/alcance", response_model=AlcancePanelOut)
def alcance(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Qué comparte esta clave. Smart Supply lo pide al guardar la clave (sirve
    de prueba) y antes de sincronizar."""
    a = panel.alcance_panel_de(db, ctx)
    t = db.get(Tenant, ctx.tenant_id)
    con = db.get(Conexion, ctx.conexion_id) if ctx.conexion_id else None
    op = panel.opciones(db, ctx.tenant_id)
    series = op["series"] if a.series is None else sorted(a.series)
    rems = op["series_remision"] if a.series_remision is None else sorted(a.series_remision)
    perfiles = op["perfiles"] if a.perfiles is None else sorted(a.perfiles)
    plazas = []
    for p in op["plazas"]:
        propias = {
            "series": sorted(set(p["series"]) & set(series)),
            "series_remision": sorted(set(p["series_remision"]) & set(rems)),
            "perfiles": sorted(set(p["perfiles"]) & set(perfiles)),
        }
        if propias["series"] or propias["series_remision"]:
            plazas.append(PlazaAlcanceOut(nombre=p["nombre"], **propias))
    return AlcancePanelOut(
        empresa=EmpresaPanelOut(id=ctx.tenant_id, nombre=(t.legal_name if t else "") or ""),
        conexion=(ConexionPanelBreveOut(id=con.id, nombre=con.nombre, pista=con.clave_pista)
                  if con else None),
        sin_limite=a.sin_limite,
        plaza=a.plaza,
        series=series,
        series_remision=rems,
        perfiles=perfiles,
        plazas=plazas,
        max_dias=_MAX_DIAS,
        max_limit=_MAX_LIMIT,
        **a.datos(),
    )


# ─── /oc ─────────────────────────────────────────────────────────────────────

def _doc_vigente():
    """(líneas del documento vigente, ¿es payload_nuevo?) como expresiones SQL.

    La versión que manda es la del documento más nuevo: `payload_nuevo` si
    trae partidas (un cambio abierto o ya resuelto), si no `payload`. Es la
    misma regla que el reporte de armado, con una excepción: cuando el cambio
    se cerró SOLO porque el documento volvió a coincidir con la remisión, el
    cliente deshizo su corrección y `payload_nuevo` guarda la versión que ya no
    existe; manda `payload`. CASE y no AND: Postgres no promete el orden de un
    AND, y `jsonb_array_length` de algo que no es arreglo truena."""
    nuevo = OCRecibida.payload_nuevo["lineas"]
    viejo = OCRecibida.payload["lineas"]
    revertido = sa.and_(OCRecibida.cambio_resuelto_at.isnot(None),
                        OCRecibida.cambio_resuelto_nota == NOTA_DOCUMENTO_REVERTIDO)
    usa_nuevo = sa.case(
        (sa.func.jsonb_typeof(nuevo) != "array", sa.false()),
        (revertido, sa.false()),
        (sa.func.jsonb_array_length(nuevo) > 0, sa.true()),
        else_=sa.false(),
    )
    doc = sa.case(
        (usa_nuevo, nuevo),
        (sa.func.jsonb_typeof(viejo) == "array", viejo),
        else_=sa.cast(sa.literal("[]"), JSONB),
    )
    return doc, usa_nuevo


def _cambio_abierto():
    return sa.and_(OCRecibida.cambio_detectado_at.isnot(None),
                   OCRecibida.cambio_resuelto_at.is_(None))


@router.get("/oc", response_model=Pagina[OCPanelOut])
def oc(
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    campo: Literal["actualizado", "recibida", "entrega"] = Query(
        "actualizado", description="Sobre qué fecha aplica desde/hasta"),
    actualizado_desde: Optional[datetime] = Query(
        None, description="Sondeo incremental: solo lo que cambió desde aquí (ISO; sin zona = UTC)"),
    estado: Optional[str] = Query(None, description="PENDIENTE,ASIGNADA,DESCARTADA"),
    canal: Optional[str] = Query(None, description="WHATSAPP,EMAIL,MANUAL,API"),
    limit: int = Query(_LIMIT, ge=1, le=_MAX_LIMIT),
    despues: Optional[str] = Query(None, description="El «siguiente» de la página anterior"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """La bitácora de órdenes de compra de la plaza, con su cadena OC →
    remisión → factura y el canal por el que entraron (incluye DESCARTADA).

    Se pide por rango (`desde`/`hasta` sobre `campo`) o por sondeo
    (`actualizado_desde`, a lo mucho 93 días atrás), o las dos. Orden y llave:
    (`actualizado_at`, `id`). Una OC que cambia mientras se pagina vuelve a
    salir más adelante; nunca se salta."""
    a = panel.alcance_panel_de(db, ctx)
    panel.requiere(a.oc, "las órdenes de compra")
    if (desde is None) != (hasta is None):
        raise HTTPException(status_code=422, detail="Manda «desde» y «hasta» juntos")
    if desde is None and actualizado_desde is None:
        raise HTTPException(status_code=422,
                            detail="Manda «desde»/«hasta» o «actualizado_desde»")
    if desde is not None:
        _rango(desde, hasta)
    if actualizado_desde is not None:
        if actualizado_desde.tzinfo is None:
            actualizado_desde = actualizado_desde.replace(tzinfo=timezone.utc)
        if actualizado_desde < datetime.now(timezone.utc) - timedelta(days=_MAX_DIAS):
            raise HTTPException(
                status_code=422,
                detail=f"«actualizado_desde» no puede ir más de {_MAX_DIAS} días atrás",
            )
    estados = _lista(estado, ESTADOS_OC, "Estado")
    canales = _lista(canal, CANALES, "Canal")
    tras = _leer_cursor(despues, _ts, UUID)

    RemSerie = aliased(Serie)
    doc, usa_nuevo = _doc_vigente()
    partidas = sa.case((sa.func.jsonb_typeof(doc) == "array", sa.func.jsonb_array_length(doc)),
                       else_=0)
    actualizado = sa.func.greatest(OCRecibida.updated_at, Remision.updated_at, Factura.updated_at)
    entrega = sa.func.coalesce(OCRecibida.fecha_entrega, Remision.fecha_entrega)
    plaza_oc = sa.func.coalesce(OCRecibida.sucursal_id, Remision.sucursal_id)
    q = (
        db.query(
            OCRecibida.id, OCRecibida.canal, OCRecibida.origen_externo, OCRecibida.folio_externo,
            OCRecibida.remitente, OCRecibida.archivo_nombre, OCRecibida.recibida_at,
            OCRecibida.estado, OCRecibida.motivo, OCRecibida.cliente_id,
            OCRecibida.punto_entrega,
            OCRecibida.cambio_detalle["resumen"].astext.label("cambio_resumen"),
            _cambio_abierto().label("cambio_abierto"),
            entrega.label("fecha_entrega"),
            partidas.label("partidas"),
            usa_nuevo.label("usa_nuevo"),
            actualizado.label("actualizado_at"),
            Cliente.legal_name.label("cliente"),
            Sucursal.nombre.label("plaza"),
            Proyecto.nombre.label("proyecto"),
            Remision.id.label("rem_id"), Remision.folio_interno.label("rem_folio"),
            Remision.estado.label("rem_estado"), Remision.fecha_entrega.label("rem_entrega"),
            Remision.total.label("rem_total"), RemSerie.codigo.label("rem_serie"),
            Factura.id.label("fac_id"), Factura.serie.label("fac_serie"),
            Factura.folio.label("fac_folio"), Factura.uuid.label("fac_uuid"),
            Factura.estado.label("fac_estado"), Factura.origen.label("fac_origen"),
        )
        .outerjoin(Remision, sa.and_(Remision.id == OCRecibida.remision_id,
                                     Remision.deleted_at.is_(None)))
        .outerjoin(RemSerie, RemSerie.id == Remision.serie_id)
        .outerjoin(Factura, sa.and_(Factura.id == Remision.factura_id,
                                    Factura.deleted_at.is_(None)))
        .outerjoin(Cliente, Cliente.id == OCRecibida.cliente_id)
        .outerjoin(Sucursal, Sucursal.id == plaza_oc)
        .outerjoin(Proyecto, Proyecto.id == OCRecibida.proyecto_id)
        .filter(OCRecibida.tenant_id == ctx.tenant_id,
                panel.filtro_oc(db, ctx.tenant_id, a, serie_remision=Remision.serie_id,
                                plaza=plaza_oc))
    )
    if desde is not None:
        if campo == "recibida":
            fecha = sa.cast(sa.func.timezone(_ZONA, OCRecibida.recibida_at), sa.Date)
        elif campo == "entrega":
            fecha = entrega
        else:
            fecha = sa.cast(sa.func.timezone(_ZONA, actualizado), sa.Date)
        q = q.filter(fecha >= desde, fecha <= hasta)
    if actualizado_desde is not None:
        q = q.filter(actualizado >= actualizado_desde)
    if estados:
        q = q.filter(OCRecibida.estado.in_(estados))
    if canales:
        q = q.filter(OCRecibida.canal.in_(canales))
    if tras is not None:
        ts, oid = tras
        q = q.filter(sa.or_(actualizado > ts,
                            sa.and_(actualizado == ts, OCRecibida.id > oid)))
    filas = q.order_by(actualizado, OCRecibida.id).limit(limit + 1).all()
    filas, siguiente = _pagina(filas, limit, lambda f: (f.actualizado_at.isoformat(), f.id))

    items = []
    for f in filas:
        items.append(OCPanelOut(
            id=f.id, canal=f.canal, origen_externo=f.origen_externo,
            perfil=panel.perfil_de(f.origen_externo), folio_externo=f.folio_externo,
            remitente=f.remitente, archivo_nombre=f.archivo_nombre, recibida_at=f.recibida_at,
            fecha_entrega=f.fecha_entrega, estado=f.estado, motivo=f.motivo,
            cliente_id=f.cliente_id, cliente=f.cliente, plaza=f.plaza,
            punto_entrega=f.punto_entrega, proyecto=f.proyecto, partidas=f.partidas or 0,
            documento="payload_nuevo" if f.usa_nuevo else "payload",
            cambio_abierto=bool(f.cambio_abierto),
            cambio_resumen=f.cambio_resumen or None,
            remision=RemisionDeOCOut(
                id=f.rem_id, folio=f.rem_folio, serie=f.rem_serie, estado=str(f.rem_estado),
                fecha_entrega=f.rem_entrega, total=str(Decimal(f.rem_total).quantize(_CENTAVO)),
            ) if f.rem_id else None,
            factura=FacturaDeOCOut(
                id=f.fac_id, serie=f.fac_serie, folio=f.fac_folio, uuid=f.fac_uuid,
                estado=str(f.fac_estado), origen=f.fac_origen,
            ) if f.fac_id else None,
            actualizado_at=f.actualizado_at,
        ))
    return Pagina[OCPanelOut](items=items, limit=limit, siguiente=siguiente)


@router.get("/oc-lineas", response_model=Pagina[OCLineaOut])
def oc_lineas(
    desde: date = Query(..., description="Fecha de ENTREGA"),
    hasta: date = Query(...),
    limit: int = Query(_LIMIT, ge=1, le=_MAX_LIMIT),
    despues: Optional[str] = Query(None, description="El «siguiente» de la página anterior"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """El volumen PEDIDO: cada partida del documento vigente de las OC vivas
    (PENDIENTE y ASIGNADA; las descartadas no se pidieron) con entrega en el
    rango. Es lo que el cliente escribió, sin cruzar: el cruce a producto es la
    remisión (`/remisionado`). Llave: (`oc_id`, `numero`)."""
    a = panel.alcance_panel_de(db, ctx)
    panel.requiere(a.oc, "las órdenes de compra")
    _rango(desde, hasta)
    tras = _leer_cursor(despues, UUID, int)

    doc, usa_nuevo = _doc_vigente()
    lineas = (
        sa.func.jsonb_array_elements(doc)
        .table_valued(sa.column("valor", JSONB), with_ordinality="numero")
        .render_derived()            # AS partida(valor, numero): sin esto no hay columnas
        .lateral("partida")
    )
    entrega = sa.func.coalesce(OCRecibida.fecha_entrega, Remision.fecha_entrega)
    plaza_oc = sa.func.coalesce(OCRecibida.sucursal_id, Remision.sucursal_id)
    q = (
        db.query(
            OCRecibida.id.label("oc_id"), lineas.c.numero, lineas.c.valor,
            usa_nuevo.label("usa_nuevo"), _cambio_abierto().label("cambio_abierto"),
            OCRecibida.origen_externo, OCRecibida.folio_externo, OCRecibida.estado,
            OCRecibida.cliente_id, entrega.label("fecha_entrega"),
            Sucursal.nombre.label("plaza"),
            Remision.id.label("rem_id"), Remision.folio_interno.label("rem_folio"),
            Remision.estado.label("rem_estado"),
        )
        .select_from(OCRecibida)
        .outerjoin(Remision, sa.and_(Remision.id == OCRecibida.remision_id,
                                     Remision.deleted_at.is_(None)))
        .outerjoin(Sucursal, Sucursal.id == plaza_oc)
        .join(lineas, sa.true())
        .filter(
            OCRecibida.tenant_id == ctx.tenant_id,
            OCRecibida.estado.in_(("PENDIENTE", "ASIGNADA")),
            entrega >= desde, entrega <= hasta,
            panel.filtro_oc(db, ctx.tenant_id, a, serie_remision=Remision.serie_id,
                            plaza=plaza_oc),
        )
    )
    if tras is not None:
        oid, n = tras
        q = q.filter(sa.or_(OCRecibida.id > oid,
                            sa.and_(OCRecibida.id == oid, lineas.c.numero > n)))
    filas = q.order_by(OCRecibida.id, lineas.c.numero).limit(limit + 1).all()
    filas, siguiente = _pagina(filas, limit, lambda f: (f.oc_id, f.numero))

    items = []
    for f in filas:
        v = f.valor if isinstance(f.valor, dict) else {}
        clave_doc = (str(v.get("clave") or "").strip()) or None
        try:
            cantidad = Decimal(str(v.get("cantidad")).strip())
            cantidad = cantidad if cantidad.is_finite() else None
        except (InvalidOperation, ValueError):
            cantidad = None
        items.append(OCLineaOut(
            oc_id=f.oc_id, numero=f.numero,
            documento="payload_nuevo" if f.usa_nuevo else "payload",
            cambio_abierto=bool(f.cambio_abierto), perfil=panel.perfil_de(f.origen_externo),
            folio_externo=f.folio_externo, fecha_entrega=f.fecha_entrega, estado=f.estado,
            cliente_id=f.cliente_id, plaza=f.plaza, remision_id=f.rem_id,
            remision_folio=f.rem_folio,
            remision_estado=str(f.rem_estado) if f.rem_estado is not None else None,
            clave_doc=clave_doc, clave=norm_clave_sae(clave_doc) or None if clave_doc else None,
            descripcion=(str(v.get("descripcion") or "").strip()) or None,
            unidad_doc=(str(v.get("unidad") or "").strip()) or None,
            cantidad=_txt(cantidad),
        ))
    return Pagina[OCLineaOut](items=items, limit=limit, siguiente=siguiente)


# ─── /remisionado ────────────────────────────────────────────────────────────

@router.get("/remisionado", response_model=Pagina[RemisionadoOut])
def remisionado(
    desde: date = Query(..., description="Fecha de ENTREGA"),
    hasta: date = Query(...),
    series: Optional[str] = Query(
        None, description="Series de REMISIÓN separadas por coma; por omisión las de la clave"),
    limit: int = Query(_LIMIT, ge=1, le=_MAX_LIMIT),
    despues: Optional[str] = Query(None, description="El «siguiente» de la página anterior"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Lo ENTREGADO: cada línea de las remisiones no canceladas de sus series de
    remisión entregadas en el rango, por producto y presentación. Facturadas o
    no (`facturada` lo dice): para los días que todavía no se facturan, esto es
    la vista previa. Llave: (`remision_id`, `numero_linea`).

    La fecha de entrega es la capturada; si la remisión no la trae (la captura
    manual no la pide), la de las notas de su factura o la de la remisión, y
    `fecha_entrega_origen` dice cuál (services/mini_conta.remisiones_entregadas,
    la misma regla que Mini Conta)."""
    a = panel.alcance_panel_de(db, ctx)
    panel.requiere(a.remisiones, "las remisiones")
    _rango(desde, hasta)
    leer = _series(series, a.series_remision)
    tras = _leer_cursor(despues, UUID, int)

    ids_series = None
    if leer is not None:
        ids_series = panel.ids_de_series(db, ctx.tenant_id, leer, "REMISION")
    rango = remisiones_entregadas(db, ctx.tenant_id, desde, hasta, serie_ids=ids_series)
    if not rango:
        return Pagina[RemisionadoOut](items=[], limit=limit, siguiente=None)

    q = (
        db.query(
            LineaRemision.remision_id, LineaRemision.numero_linea, LineaRemision.presentacion,
            LineaRemision.cantidad_solicitada, LineaRemision.cantidad_surtida,
            LineaRemision.precio_unitario, LineaRemision.importe,
            Remision.folio_interno, Remision.estado,
            Remision.factura_id, Remision.su_pedido, Remision.nota_entrega,
            Remision.subtotal, Remision.descuento, Remision.cliente_facturacion_id,
            Serie.codigo.label("serie"),
            Cliente.legal_name.label("cliente"),
            Sucursal.nombre.label("plaza"),
            Factura.estado.label("factura_estado"),
            Producto.id.label("producto_id"), Producto.sku, Producto.nombre.label("producto"),
            Producto.clave_sae, Producto.presentaciones, Producto.unidad_base,
            Producto.peso_variable,
        )
        .join(Remision, Remision.id == LineaRemision.remision_id)
        .join(Producto, Producto.id == LineaRemision.producto_id)
        .outerjoin(Serie, Serie.id == Remision.serie_id)
        .outerjoin(Cliente, Cliente.id == Remision.cliente_facturacion_id)
        .outerjoin(Sucursal, Sucursal.id == Remision.sucursal_id)
        .outerjoin(Factura, sa.and_(Factura.id == Remision.factura_id,
                                    Factura.deleted_at.is_(None)))
        .filter(
            Remision.tenant_id == ctx.tenant_id,
            LineaRemision.remision_id.in_(list(rango)),
        )
    )
    if tras is not None:
        rid, n = tras
        q = q.filter(sa.or_(LineaRemision.remision_id > rid,
                            sa.and_(LineaRemision.remision_id == rid,
                                    LineaRemision.numero_linea > n)))
    filas = (q.order_by(LineaRemision.remision_id, LineaRemision.numero_linea)
             .limit(limit + 1).all())
    filas, siguiente = _pagina(filas, limit, lambda f: (f.remision_id, f.numero_linea))

    items = []
    for f in filas:
        surtida = Decimal(f.cantidad_surtida) if f.cantidad_surtida is not None else None
        if f.peso_variable and surtida is not None and surtida > 0:
            # El peso real: viene en la unidad base, no en la presentación.
            cantidad, unidad = surtida, f.unidad_base
        else:
            cantidad, unidad = Decimal(f.cantidad_solicitada or 0), f.presentacion
        factor, kg, estimado = _kg(unidad, f.unidad_base, f.presentaciones, cantidad)
        importe = Decimal(f.importe or 0)
        sub = Decimal(f.subtotal or 0)
        if f.descuento and sub > 0:
            importe = importe * (1 - Decimal(f.descuento) / sub)
        propia = claves_sae_por_presentacion(f.presentaciones).get(
            str(f.presentacion or "").strip().upper())
        entrega, origen = rango[f.remision_id]
        items.append(RemisionadoOut(
            remision_id=f.remision_id, numero_linea=f.numero_linea, folio=f.folio_interno,
            serie=f.serie, estado=str(f.estado), fecha_entrega=entrega,
            fecha_entrega_origen=origen,
            factura_id=f.factura_id, facturada=f.factura_estado == "TIMBRADA",
            su_pedido=f.su_pedido, cliente_id=f.cliente_facturacion_id, cliente=f.cliente,
            plaza=f.plaza, punto_entrega=f.nota_entrega,
            producto_id=f.producto_id, sku=f.sku, producto=f.producto,
            clave=propia or f.clave_sae, clave_producto=f.clave_sae,
            presentacion=f.presentacion,
            cantidad_solicitada=_txt(f.cantidad_solicitada),
            cantidad_surtida=_txt(f.cantidad_surtida),
            cantidad=_txt(cantidad), unidad=unidad or f.presentacion,
            factor_kg=_txt(factor), kg=_txt(kg), kg_estimado=estimado,
            precio_unitario=_txt(f.precio_unitario),
            importe=str(importe.quantize(_CENTAVO)),
        ))
    return Pagina[RemisionadoOut](items=items, limit=limit, siguiente=siguiente)


# ─── /facturado ──────────────────────────────────────────────────────────────

@router.get("/facturado", response_model=Pagina[FacturadoOut])
def facturado(
    desde: date = Query(...),
    hasta: date = Query(...),
    fecha: Literal["entrega", "factura"] = Query(
        "entrega", description="entrega = el día que salió la mercancía; factura = el del timbre"),
    series: Optional[str] = Query(
        None, description="Series de FACTURA separadas por coma; por omisión las de la clave"),
    limit: int = Query(_LIMIT, ge=1, le=_MAX_LIMIT),
    despues: Optional[str] = Query(None, description="El «siguiente» de la página anterior"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Las líneas de las facturas timbradas de ingreso (nativas y espejo de SAE)
    de sus series, por fecha de ENTREGA (por omisión) o de factura.

    La fecha de entrega sale de la remisión ligada, de las notas o de la factura
    (`fecha_entrega_origen`), la misma regla que Mini Conta. El producto es el
    que guardó la línea; una partida que no cruzó sale con `producto_id` null y
    su `clave_sae`. Llave: (`factura_id`, `numero_linea`), que sobrevive a que el
    espejo recree las líneas (el `id` de línea no)."""
    a = panel.alcance_panel_de(db, ctx)
    _rango(desde, hasta)
    leer = _series(series, a.series)
    tras = _leer_cursor(despues, UUID, int)

    rango = panel.facturas_del_rango(db, ctx.tenant_id, leer, desde, hasta, fecha)
    if not rango:
        return Pagina[FacturadoOut](items=[], limit=limit, siguiente=None)
    q = (
        db.query(
            LineaFactura.factura_id, LineaFactura.numero_linea, LineaFactura.producto_id,
            LineaFactura.clave_sae, LineaFactura.descripcion, LineaFactura.presentacion,
            LineaFactura.clave_unidad, LineaFactura.cantidad, LineaFactura.importe,
            LineaFactura.descuento,
            Factura.origen, Factura.espejo_empresa, Factura.serie, Factura.folio,
            Factura.uuid, Factura.cliente_id, Factura.su_pedido,
            Cliente.legal_name.label("cliente"),
            Producto.sku, Producto.nombre.label("producto"), Producto.unidad_base,
            Producto.presentaciones,
        )
        .join(Factura, Factura.id == LineaFactura.factura_id)
        .outerjoin(Cliente, Cliente.id == Factura.cliente_id)
        .outerjoin(Producto, Producto.id == LineaFactura.producto_id)
        .filter(Factura.tenant_id == ctx.tenant_id,
                LineaFactura.factura_id.in_(list(rango)))
    )
    if tras is not None:
        fid, n = tras
        q = q.filter(sa.or_(LineaFactura.factura_id > fid,
                            sa.and_(LineaFactura.factura_id == fid,
                                    LineaFactura.numero_linea > n)))
    filas = (q.order_by(LineaFactura.factura_id, LineaFactura.numero_linea)
             .limit(limit + 1).all())
    filas, siguiente = _pagina(filas, limit, lambda f: (f.factura_id, f.numero_linea))

    remisiones: dict = {}
    pagina_ids = {f.factura_id for f in filas}
    if pagina_ids:
        for rid, fid in (
            db.query(Remision.id, Remision.factura_id)
            .filter(Remision.factura_id.in_(list(pagina_ids)), Remision.deleted_at.is_(None))
            .order_by(Remision.fecha_entrega, Remision.id)
        ):
            remisiones.setdefault(fid, []).append(rid)

    items = []
    for f in filas:
        fecha_factura, entrega, origen = rango[f.factura_id]
        cantidad = Decimal(f.cantidad)
        unidad = (f.presentacion or "").strip().upper() or ("KILO" if f.clave_unidad == "KGM" else None)
        factor, kg, estimado = _kg(unidad, f.unidad_base, f.presentaciones, cantidad)
        importe = (Decimal(f.importe or 0) - Decimal(f.descuento or 0)).quantize(_CENTAVO)
        items.append(FacturadoOut(
            factura_id=f.factura_id, numero_linea=f.numero_linea, origen=f.origen,
            espejo_empresa=f.espejo_empresa, serie=f.serie, folio=f.folio, uuid=f.uuid,
            fecha_factura=fecha_factura, fecha_entrega=entrega, fecha_entrega_origen=origen,
            cliente_id=f.cliente_id, cliente=f.cliente, su_pedido=f.su_pedido,
            remision_ids=remisiones.get(f.factura_id, []),
            producto_id=f.producto_id, sku=f.sku, producto=f.producto, clave_sae=f.clave_sae,
            descripcion=f.descripcion, presentacion=f.presentacion,
            clave_unidad=f.clave_unidad, unidad=unidad,
            cantidad=_txt(cantidad), factor_kg=_txt(factor), kg=_txt(kg), kg_estimado=estimado,
            importe=str(importe),
        ))
    return Pagina[FacturadoOut](items=items, limit=limit, siguiente=siguiente)


# ─── /catalogo ───────────────────────────────────────────────────────────────

def _presentaciones(raw) -> dict[str, PresentacionOut]:
    out: dict[str, PresentacionOut] = {}
    for nombre, v in (raw or {}).items():
        k = str(nombre).strip().upper()
        if not k:
            continue
        d = v if isinstance(v, dict) else {}
        clave = str(d.get("clave_sae") or "").strip().upper() or None
        out[k] = PresentacionOut(
            factor=_txt(_factor(v)), clave_sae=clave,
            sat=(str(d.get("sat")).strip() or None) if d.get("sat") else None,
            estimado=bool(d.get("estimado")),
        )
    return out


@router.get("/catalogo", response_model=Pagina[ProductoPanelOut])
def catalogo(
    limit: int = Query(_LIMIT, ge=1, le=_MAX_LIMIT),
    despues: Optional[str] = Query(None, description="El «siguiente» de la página anterior"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Los productos (activos y desactivados; los borrados no) con su clave de
    SAE y la de cada presentación, y el factor de cada una. Es con lo que Smart
    Supply junta un conteo de bodega (por clave) con lo remisionado y lo
    facturado. Sin precios ni costos. Llave: `id`."""
    a = panel.alcance_panel_de(db, ctx)
    panel.requiere(a.catalogo, "el catálogo")
    tras = _leer_cursor(despues, UUID)
    q = (
        db.query(
            Producto.id, Producto.sku, Producto.nombre, Producto.unidad_base,
            Producto.unidad_sat, Producto.peso_variable, Producto.activo, Producto.clave_sae,
            Producto.presentaciones, CategoriaProducto.nombre.label("categoria"),
        )
        .outerjoin(CategoriaProducto, CategoriaProducto.id == Producto.categoria_id)
        .filter(Producto.tenant_id == ctx.tenant_id, Producto.deleted_at.is_(None))
    )
    if tras is not None:
        q = q.filter(Producto.id > tras[0])
    filas = q.order_by(Producto.id).limit(limit + 1).all()
    filas, siguiente = _pagina(filas, limit, lambda f: (f.id,))
    items = [
        ProductoPanelOut(
            id=f.id, sku=f.sku, nombre=f.nombre, categoria=f.categoria,
            unidad_base=f.unidad_base, unidad_sat=f.unidad_sat,
            peso_variable=bool(f.peso_variable), activo=bool(f.activo),
            clave_sae=(f.clave_sae or "").strip().upper() or None,
            presentaciones=_presentaciones(f.presentaciones),
        )
        for f in filas
    ]
    return Pagina[ProductoPanelOut](items=items, limit=limit, siguiente=siguiente)
