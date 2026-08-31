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

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import (
    Almacen,
    Cliente,
    GrupoWhatsapp,
    ListaPrecios,
    OCRecibida,
    Producto,
    Proyecto,
    Remision,
    Sucursal,
)
from ...schemas.common import Page
from ...schemas.oc_recibida import (
    CrearRemisionIn,
    OCRecibidaDetailOut,
    OCRecibidaIn,
    OCRecibidaOut,
    OCRecibidaUpdate,
)
from ...models import ProductoCliente
from ...services import cliente_match
from ...services.precios import resolver_precio
from ...services.series import resolver_almacen, resolver_serie
from ...services.producto_match import (
    aprender_alias_con_alcance,
    alias_de_cliente,
    alias_del_tenant,
    buscar,
    normalizar_catalogo,
    normalizar_unidad,
    productos_activos,
)
from ._helpers import ensure_fk, get_or_404, paginate

router = APIRouter(prefix="/oc-recibidas", tags=["bandeja de OC"])

# Su propio menú desde 0057: compartir permiso con remisiones dejaba la
# bandeja (órdenes de TODOS los clientes) a la vista de cualquier rol que
# solo debía ver remisiones. Los roles con menu:remisiones lo recibieron en
# la migración; la conexión del bot lo trae en PERMISOS_CONEXION.
_READ = "menu:oc"
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


def _proyecto_de(db: Session, tenant_id, payload: dict):
    """El proyecto del catálogo al que apunta la clave PROYECTO del documento.

    Es lo que hace que una orden que dice "HOSPITALES" entre ya etiquetada con
    la negociación —y por lo tanto con sus precios— sin que nadie capture nada.
    Devuelve None si la clave no está dada de alta o no tiene proyecto enlazado;
    entonces el operador lo elige en la bandeja y ahí se aprende.
    """
    perfil = (payload.get("perfil") or "").strip().lower()
    proyecto = (payload.get("proyecto") or "").strip()
    if not perfil or not proyecto:
        return None
    fila = cliente_match.buscar_equivalencia(
        db, tenant_id, "PROYECTO", f"{perfil}:{proyecto}", solo_confirmadas=True
    )
    return fila.proyecto_id if fila is not None else None


def _grupo_apagado(db: Session, jid: str) -> bool:
    """¿El dueño apagó este grupo desde la pantalla de Conexiones?"""
    if not jid:
        return False
    g = db.query(GrupoWhatsapp).filter(GrupoWhatsapp.jid == jid).one_or_none()
    return g is not None and not g.activo


def _resolver_y_aplicar(db: Session, oc: OCRecibida) -> None:
    """(Re)resuelve cliente y destino de una OC desde su payload."""
    payload = oc.payload or {}

    # Grupo apagado desde el Facturador: la orden NO se pierde —eso sería peor
    # que el problema— pero tampoco ensucia la bandeja. Queda descartada con el
    # motivo escrito y se puede reabrir de un clic.
    if _grupo_apagado(db, str(payload.get("jid") or "")):
        oc.estado = "DESCARTADA"
        oc.motivo = "Este grupo está apagado en Conexiones; nada se procesó de él"
        oc.cliente_id = None
        oc.sucursal_id = None
        oc.candidatos = None
        oc.punto_entrega = (payload.get("ubicacion") or "").strip() or None
        return

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
    oc.proyecto_id = _proyecto_de(db, oc.tenant_id, payload)

    if res.cliente_id is not None:
        if oc.punto_entrega:
            oc.sucursal_id = cliente_match.resolver_destino(
                db, oc.tenant_id, res.cliente_id, oc.punto_entrega,
                perfil=str(payload.get("perfil") or ""),
            )
        # Último recurso: la sucursal por defecto del grupo. Cubre el hospital
        # nuevo y la orden que no dice a dónde va — la entrega tiene que salir
        # de algún lado igual.
        if oc.sucursal_id is None:
            oc.sucursal_id = cliente_match.sucursal_del_grupo(
                db, oc.tenant_id, str(payload.get("jid") or ""), res.cliente_id
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
            # La captura ya no se toca, pero el puntero al documento original
            # sí se completa si faltaba: 183 OCs de la migración llegaron sin
            # su link de Drive y "Ver la OC original" no tenía a dónde ir.
            if payload.archivo_url and not existente.archivo_url:
                existente.archivo_url = payload.archivo_url
                if payload.archivo_nombre and not existente.archivo_nombre:
                    existente.archivo_nombre = payload.archivo_nombre
                existente.updated_by = ctx.user_id
                db.flush()
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
    # Rango sobre la fecha de RECEPCIÓN (no la del documento): es como el
    # operador piensa la bandeja — "lo que llegó hoy", "lo de esta semana".
    fecha_desde: Optional[date] = Query(default=None),
    fecha_hasta: Optional[date] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(OCRecibida)
    if ctx.cliente_scope:
        # Candado del portal: solo órdenes de SUS clientes.
        query = query.filter(OCRecibida.cliente_id.in_(ctx.cliente_scope))
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
    if fecha_desde:
        query = query.filter(OCRecibida.recibida_at >= fecha_desde)
    if fecha_hasta:
        # Inclusivo: "hasta el 28" incluye todo el día 28, por eso el < al día
        # siguiente en vez de un <= que dejaría fuera la tarde.
        query = query.filter(OCRecibida.recibida_at < fecha_hasta + timedelta(days=1))
    return paginate(query.order_by(OCRecibida.recibida_at.desc()), OCRecibidaOut, limit, offset)


def _norm_codigo(v: str) -> str:
    """'PIÑA -FRUT-350' cruza con 'PINA-FRUT-350': mayúsculas, sin acentos ni
    espacios. Misma tolerancia que el cruce por clave del Master (PR #32)."""
    import unicodedata
    s = unicodedata.normalize("NFKD", v or "").encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in s.upper() if ch.isalnum() or ch == "-")


def _codigos_para(db: Session, cliente_id) -> tuple[dict, dict, dict]:
    """Los códigos del catálogo por cliente, listos para el cruce por clave.

    Devuelve (del_cliente, de_otros, presentacion_del_cliente):
      - del_cliente: {codigo_norm: producto_id | None} de ESTE cliente — decide
        al 100; None marca código ambiguo y NO decide.
      - de_otros:    {codigo_norm: producto_id | None} del resto del tenant;
        None marca código ambiguo (dos productos) y NO decide — regla PR #32.
      - presentacion_del_cliente: {producto_id: presentacion} para partidas que
        no dicen unidad: la unidad con la que ese cliente compra ese producto.

    La ambigüedad se vigila en LAS DOS ramas: `producto_clientes` solo es único
    por (tenant, cliente, producto), así que nada impide que un cliente tenga
    el mismo código en dos productos (una renumeración suya, o un OCR que
    recortó la clave). Sin la guarda, el ganador lo elegía el orden físico de
    la tabla — y decidía al 100.
    """
    del_cliente: dict[str, object] = {}
    de_otros: dict[str, object] = {}
    pres_cliente: dict[UUID, str] = {}
    q = db.query(ProductoCliente)
    if cliente_id is not None:
        # Solo lo que este cruce puede usar: el catálogo del cliente y el de
        # quienes comparten códigos. Sin filtro, cada apertura de OC traía la
        # tabla entera.
        q = q.filter(ProductoCliente.codigo_cliente.isnot(None))
    for pc in q.all():
        cod = _norm_codigo(pc.codigo_cliente or "")
        if not cod:
            continue
        if cliente_id is not None and pc.cliente_id == cliente_id:
            # El None es pegajoso: una tercera fila lo mantiene ambiguo.
            if cod in del_cliente and del_cliente[cod] != pc.producto_id:
                del_cliente[cod] = None
            else:
                del_cliente.setdefault(cod, pc.producto_id)
            if pc.presentacion:
                pres_cliente[pc.producto_id] = pc.presentacion
        else:
            if cod in de_otros and de_otros[cod] != pc.producto_id:
                de_otros[cod] = None   # ambiguo: dos productos, nadie decide
            else:
                de_otros.setdefault(cod, pc.producto_id)
    return del_cliente, de_otros, pres_cliente


def _detalle(db: Session, oc: OCRecibida) -> dict:
    """Detalle + cruce de productos sugerido para cada partida.

    El cruce se calcula al vuelo (no se persiste): el catálogo cambia, y una
    sugerencia guardada hace meses miente. `productos_activos` se carga una vez
    para las N partidas en vez de una vez por partida.

    Orden del cruce por partida: 1) clave en el catálogo DEL cliente (100),
    2) la misma clave usada por otro cliente si no es ambigua (95), 3) alias del
    cliente > alias global > exacto > difuso (con la unidad como freno del
    difuso). `auto` resume si la orden entera cruzó por vías deterministas y
    puede volverse remisión con un clic.
    """
    payload = oc.payload or {}
    lineas_raw = payload.get("lineas") or []
    catalogo = productos_activos(db) if lineas_raw else []
    # Precalculado una vez para las N partidas: si no, el cruce es
    # O(partidas × productos) normalizaciones por cada apertura de la orden.
    norms = normalizar_catalogo(catalogo) if catalogo else {}
    aliases = alias_del_tenant(db) if catalogo else {}
    aliases_cli = alias_de_cliente(db, oc.cliente_id, oc.sucursal_id) if catalogo else {}
    cods_cli, cods_otros, pres_cli = (
        _codigos_para(db, oc.cliente_id) if catalogo else ({}, {}, {})
    )
    by_id = {p.id: p for p in catalogo}
    lineas = []
    for i, ln in enumerate(lineas_raw, start=1):
        texto = str(ln.get("descripcion") or "")
        unidad_norm = normalizar_unidad(str(ln.get("unidad") or ""))
        cands = (
            buscar(db, oc.tenant_id, texto, limit=5, prods=catalogo, aliases=aliases,
                   aliases_cliente=aliases_cli, norms=norms, unidad=unidad_norm)
            if texto else []
        )
        # La CLAVE del cliente es lo más preciso que trae el documento. Primero
        # exacta contra el catálogo del cliente; luego la de otro cliente (Balles
        # y Jubran comparten claves) si no es ambigua; al final, como texto.
        clave = str(ln.get("clave") or "").strip()
        if clave:
            cod = _norm_codigo(clave)
            exactos = []
            pid = cods_cli.get(cod)
            if pid is not None and pid in by_id:
                exactos.append(_cand_de(by_id[pid], 100, "codigo_cliente"))
            elif cods_otros.get(cod) is not None and cods_otros[cod] in by_id:
                exactos.append(_cand_de(by_id[cods_otros[cod]], 95, "codigo_otro_cliente"))
            por_clave = buscar(db, oc.tenant_id, clave, limit=3, prods=catalogo,
                               aliases=aliases, aliases_cliente=aliases_cli, norms=norms,
                               unidad=unidad_norm)   # mismo freno papa/papaya
            vistos = {c.producto_id for c in exactos}
            # La descripción manda cuando cruzó por vía determinista: un parecido
            # sobre la clave (prefijo 96, difuso) jamás desplaza a un exacto o a
            # un alias de 100 — "PAPA" como clave no puede tapar "PAPAYA MARADOL".
            fuertes = [c for c in cands
                       if c.origen in ("exacto", "alias") and c.producto_id not in vistos]
            vistos |= {c.producto_id for c in fuertes}
            por_clave = [c for c in por_clave if c.producto_id not in vistos]
            vistos |= {c.producto_id for c in por_clave}
            cands = exactos + fuertes + por_clave + [
                c for c in cands if c.producto_id not in vistos
            ]
        top = cands[0] if cands else None
        # La presentación con la que entraría la línea: la unidad del documento;
        # si no dice (o dice una que no reconocemos), la habitual de ese cliente
        # y en último caso la del producto — pero eso ES una adivinanza y se
        # marca como tal: el factor de la presentación cambia cantidad y precio.
        pres_sugerida = unidad_norm
        pres_adivinada = False
        if pres_sugerida is None and top is not None:
            pres_sugerida = pres_cli.get(top.producto_id) or top.presentacion_default or top.unidad_base
            pres_adivinada = True
        lineas.append({
            "numero": i,
            "descripcion": texto,
            "cantidad": ln.get("cantidad") or 0,
            "unidad": ln.get("unidad"),
            "clave": ln.get("clave"),
            "precio": ln.get("precio"),
            "notas": ln.get("notas"),
            "presentacion_sugerida": pres_sugerida,
            "presentacion_adivinada": pres_adivinada,
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
    out["auto"] = _auto_de(db, oc, lineas, by_id)
    return out


def _cand_de(p: Producto, score: int, origen: str):
    """Producto → Candidato para las rutas que no pasan por `buscar`."""
    from ...services.producto_match import Candidato
    return Candidato(
        producto_id=p.id, sku=p.sku, nombre=p.nombre, score=score, origen=origen,
        presentaciones=p.presentaciones or {},
        presentacion_default=p.presentacion_default,
        unidad_base=p.unidad_base,
        categoria_id=p.categoria_id,
        esquema_impuesto_id=p.esquema_impuesto_id,
        clave_sat=p.clave_sat,
        unidad_sat=p.unidad_sat,
    )


# Orígenes que DECIDEN solos: la clave del cliente, un alias aprendido o la
# coincidencia exacta. El difuso y la IA sugieren pero jamás deciden — regla
# del catálogo multicliente (el falso positivo papa/papaya es la razón).
_ORIGENES_DETERMINISTAS = {"codigo_cliente", "alias", "exacto"}


def _auto_de(db: Session, oc: OCRecibida, lineas: list[dict], by_id: dict) -> dict:
    """¿La orden entera puede volverse remisión sin humano? Y con qué líneas.

    Reglas: cliente resuelto sin ambigüedad, TODAS las partidas cruzadas por
    vía determinista, unidad traducible a una presentación del producto, y
    precio que salga de una lista NEGOCIADA (no de la lista base: facturar a
    precio no pactado es peor que preguntar). El precio del documento, si viene,
    debe coincidir con el de la lista — discrepancia = PRECIO EN CONFLICTO.
    """
    def no(motivo: str) -> dict:
        return {"ok": False, "motivo": motivo, "lineas": [], "problemas": []}

    if oc.remision_id is not None:
        return no("Esta orden ya generó su remisión")
    if oc.estado != "PENDIENTE":
        return no("Solo una orden pendiente se convierte en automática")
    if oc.cliente_id is None or oc.ambiguo:
        return no("El cliente no está resuelto sin ambigüedad")
    if not lineas:
        return no("El documento no trae partidas legibles")

    p = oc.payload or {}
    # La MISMA serie con la que `create_remision` va a foliar: la del grupo si la
    # declara y, si no, la que resuelve el backend (sucursal → cliente → default).
    # Precificar con otra rompe la invariante que el propio create_remision
    # declara — la serie decide el folio Y qué lista de precios aplica.
    serie_id = cliente_match.serie_del_grupo(
        db, oc.tenant_id, str(p.get("jid") or ""), oc.cliente_id, "serie_remision_id"
    )
    if serie_id is None:
        serie = resolver_serie(
            db, oc.tenant_id, "REMISION",
            sucursal_id=oc.sucursal_id, cliente_id=oc.cliente_id,
        )
        serie_id = serie.id if serie is not None else None
    out_lineas = []
    # Los problemas se JUNTAN, no se reporta solo el primero: el operador
    # arreglaba uno, volvía a guardar y aparecía el siguiente. Cada uno trae el
    # `numero` de su partida para que la pantalla pueda pintarla en rojo, y el
    # conflicto de precio trae además las dos cifras y de qué lista sale la suya
    # — sin eso el aviso decía "la lista dice 57.50" sin decir cuál lista.
    problemas: list[dict] = []
    # Nombre de cada lista, resuelto UNA vez por evaluación: una orden con 20
    # conflictos contra la misma lista no la consulta 20 veces (la BD es remota).
    fuentes: dict[str, str] = {}

    def falla(ln: dict, tipo: str, mensaje: str, **extra) -> None:
        problemas.append({"numero": ln["numero"], "tipo": tipo, "mensaje": mensaje, **extra})

    for ln in lineas:
        cands = ln.get("candidatos") or []
        top = cands[0] if cands else None
        etiqueta = f"«{(ln.get('descripcion') or ln.get('clave') or '')[:60]}»"
        if top is None or top["origen"] not in _ORIGENES_DETERMINISTAS or top["score"] < 100:
            falla(ln, "sin_cruce",
                  f"La partida {ln['numero']} {etiqueta} no cruza por clave, alias ni exacto")
            continue
        # `buscar` emite a PROPÓSITO un 100 por CADA producto que normaliza igual
        # ("CILANTRO" y "Cilantro" son dos filas) para que un humano vea el
        # duplicado. Esa ambigüedad no la puede resolver el orden del seq scan:
        # misma regla que la clave de otro cliente, que ya no decide si es ambigua.
        empatados = {
            c["producto_id"] for c in cands
            if c["score"] >= 100 and c["origen"] in _ORIGENES_DETERMINISTAS
        }
        if len(empatados) > 1:
            falla(ln, "ambiguo",
                  f"La partida {ln['numero']} {etiqueta} cruza al 100 con "
                  f"{len(empatados)} productos distintos: elige cuál va")
            continue
        prod = by_id.get(top["producto_id"])
        pres = ln.get("presentacion_sugerida")
        presentaciones = set((prod.presentaciones or {}).keys()) | {prod.unidad_base}
        if not pres or pres not in presentaciones:
            falla(ln, "unidad",
                  f"La partida {ln['numero']} {etiqueta} pide una unidad "
                  f"({ln.get('unidad') or 'sin unidad'}) que el producto no vende")
            continue
        # La presentación ADIVINADA sugiere, no decide: el documento no dijo
        # unidad (o dijo una que no reconocemos) y el factor cambia cantidad y
        # precio — 10 MANOJO no es 10 KILO. Si el producto se vende de una sola
        # forma no hay nada que adivinar y la orden sigue siendo automática.
        if ln.get("presentacion_adivinada") and len(presentaciones) > 1:
            falla(ln, "unidad",
                  f"La partida {ln['numero']} {etiqueta} no trae una unidad que se "
                  f"reconozca ({ln.get('unidad') or 'sin unidad'}) y el producto se "
                  "vende en varias presentaciones: confírmala a mano")
            continue
        res = resolver_precio(
            db,
            producto_id=prod.id,
            presentacion=pres,
            cantidad=Decimal(str(ln.get("cantidad") or 1)),
            cliente_id=oc.cliente_id,
            sucursal_id=oc.sucursal_id,
            serie_id=serie_id,
            proyecto_id=oc.proyecto_id,
        )
        if res is None:
            falla(ln, "sin_precio",
                  f"La partida {ln['numero']} {etiqueta} no tiene precio en ninguna lista")
            continue
        if res.get("origen") == "lista_base":
            falla(ln, "precio_base",
                  f"La partida {ln['numero']} {etiqueta} solo tiene precio en la lista "
                  "base, no en una lista negociada del cliente")
            continue
        precio_doc = ln.get("precio")
        if precio_doc is not None and abs(Decimal(str(precio_doc)) - Decimal(res["precio"])) > Decimal("0.01"):
            clave_fuente = str(res.get("lista_id") or res.get("origen") or "")
            fuente = fuentes.get(clave_fuente)
            if fuente is None:
                fuente = fuentes[clave_fuente] = _fuente_precio(db, res)
            falla(
                ln, "precio_conflicto",
                f"La partida {ln['numero']} {etiqueta} viene con {precio_doc} en el "
                f"documento y {fuente} dice {Decimal(res['precio']):.2f}: elige cuál se cobra",
                precio_documento=str(precio_doc),
                precio_lista=str(Decimal(res["precio"]).quantize(Decimal("0.01"))),
                fuente_precio=fuente,
            )
            continue
        out_lineas.append({
            "numero": ln["numero"],
            "producto_id": str(prod.id),
            "nombre": prod.nombre,
            "presentacion": pres,
            "cantidad": str(ln.get("cantidad") or 1),
            "precio_unitario": str(res["precio"]),
            "precio_origen": res.get("origen"),
            "texto_original": ln.get("descripcion"),
            "clave": ln.get("clave"),
            "cruzo_por": top["origen"],
        })
    if problemas:
        # `motivo` sigue siendo el primero: es lo que la pantalla resume arriba.
        return {"ok": False, "motivo": problemas[0]["mensaje"], "lineas": [], "problemas": problemas}
    return {"ok": True, "motivo": None, "lineas": out_lineas, "problemas": []}


def _fuente_precio(db: Session, res: dict) -> str:
    """De dónde salió el precio, con el NOMBRE de la lista cuando lo hay.

    «la lista dice 57.50» obligaba a adivinar cuál de las listas del cliente
    habló. Con el nombre, el operador va directo a corregirla si está mal."""
    origen = res.get("origen") or ""
    if origen == "override_sucursal":
        return "el precio especial de la sucursal"
    if origen == "override_cliente":
        return "el precio especial del cliente"
    lista_id = res.get("lista_id")
    if lista_id:
        lp = (
            db.query(ListaPrecios)
            .filter(ListaPrecios.id == lista_id, ListaPrecios.deleted_at.is_(None))
            .one_or_none()
        )
        if lp is not None:
            return f"la lista «{lp.nombre}»"
    return "la lista de precios"


@router.get("/{oc_id}", response_model=OCRecibidaDetailOut)
def detalle(
    oc_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    oc = get_or_404(db, OCRecibida, oc_id, soft=False)
    if not ctx.cliente_permitido(oc.cliente_id):
        raise HTTPException(status_code=404, detail="Orden no encontrada")
    return _detalle(db, oc)


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
    if "proyecto_id" in data:
        ensure_fk(db, Proyecto, data["proyecto_id"], "proyecto_id")
        oc.proyecto_id = data["proyecto_id"]
    if "folio_externo" in data:
        oc.folio_externo = data["folio_externo"]
    if "punto_entrega" in data:
        oc.punto_entrega = (data["punto_entrega"] or "").strip() or None
    if "motivo" in data:
        oc.motivo = data["motivo"]
    elif (
        oc.sucursal_id is not None
        and oc.motivo
        and "sucursal" in oc.motivo.lower()
    ):
        # El motivo es lo que la bandeja le enseña al operador. Si decía «falta
        # decir a qué sucursal pertenece X» y la sucursal acaba de asignarse,
        # dejarlo ahí manda a revisar algo que ya está resuelto (pasó con 33
        # órdenes el 29-ago). Se limpia solo cuando la causa desapareció.
        oc.motivo = None
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
        # El proyecto que acaba de elegir el humano se guarda EN la equivalencia
        # PROYECTO: la próxima orden que diga "HOSPITALES" ya entra etiquetada.
        if oc.proyecto_id is not None:
            perfil = str((oc.payload or {}).get("perfil") or "").strip().lower()
            nombre_proy = str((oc.payload or {}).get("proyecto") or "").strip()
            if perfil and nombre_proy:
                fila = cliente_match.aprender(
                    db, ctx.tenant_id, "PROYECTO", f"{perfil}:{nombre_proy}",
                    oc.cliente_id, origen="MANUAL", confianza="CONFIRMADA",
                    user_id=ctx.user_id,
                )
                if fila is not None:
                    fila.proyecto_id = oc.proyecto_id

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
    # Si no se eligió almacén, se resuelve como la serie: sucursal → cliente →
    # predeterminado. El bot no tiene cómo elegirlo, y dejarlo vacío significaría
    # que la remisión no descuenta inventario sin que nadie lo haya decidido.
    almacen_id = resolver_almacen(
        db, ctx.tenant_id,
        almacen_id=payload.almacen_id,
        sucursal_id=oc.sucursal_id,
        cliente_id=oc.cliente_id,
    )
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

    # La serie del GRUPO gana sobre la del cliente: un cliente usa varias según
    # la operación por la que entró el pedido. Vacío = que la resuelva el
    # backend como siempre (sucursal → cliente → default).
    serie_id = cliente_match.serie_del_grupo(
        db, ctx.tenant_id, str(p.get("jid") or ""), oc.cliente_id, "serie_remision_id"
    )
    body = RemisionCreate(
        cliente_facturacion_id=oc.cliente_id,
        sucursal_id=oc.sucursal_id,
        serie_id=serie_id,
        proyecto_id=oc.proyecto_id,
        almacen_id=almacen_id,
        fecha_remision=payload.fecha_remision,
        fecha_entrega=payload.fecha_entrega or _fecha(p.get("fecha_entrega")),
        # El número de la OC ES «su pedido»: la referencia con la que el cliente
        # concilia. Sin esto solo vivía enterrado en las notas.
        su_pedido=folio[:30] or None,
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
    # diga "JITOMATE SALADET" ya sabe a qué producto va. La regla de alcance
    # protege el catálogo sola: un texto nuevo se aprende GLOBAL; un texto que
    # ya apunta a OTRO producto se aprende solo para ESTE cliente, sin tocar el
    # global — por eso basta poder leer productos (la conexión del bot lo tiene)
    # y no hace falta `producto:gestionar`, que sigue reservado para reapuntar.
    if ctx.has("producto:gestionar") or ctx.has("menu:productos"):
        for ln in payload.lineas:
            if ln.texto_original:
                aprender_alias_con_alcance(
                    db, ctx.tenant_id, ln.texto_original, ln.producto_id,
                    cliente_id=oc.cliente_id, sucursal_id=oc.sucursal_id,
                    origen="IMPORT", user_id=ctx.user_id,
                )
    # La CLAVE que traía el documento se registra como código del cliente (solo
    # si ese producto aún no tiene código para él): la próxima OC con esa clave
    # cruza al 100 sin leer siquiera la descripción. Exige `producto:gestionar`
    # —el mismo permiso que POST /productos/catalogo-cliente-batch— porque
    # `codigo_cliente` y `nombre_cliente` SON el NoIdentificacion y la
    # Descripcion de todos los CFDI futuros de ese cliente: dejar que los fije
    # el texto del OCR, sin nadie del catálogo aprobándolo, es firmar ante el
    # SAT lo que dijo una foto de WhatsApp.
    if oc.cliente_id is not None and ctx.has("producto:gestionar"):
        _registrar_codigos(db, ctx, oc, payload.lineas)

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


def _registrar_codigos(db: Session, ctx: AuthContext, oc: OCRecibida, lineas) -> None:
    """Guarda la clave del documento como código del cliente para su producto.

    SOLO cuando el producto aún no tiene código para ese cliente: el código
    preferido de salida (el que se imprime y timbra) no se pisa desde la
    bandeja — eso es tarea del catálogo del cliente, con permiso de catálogo.
    """
    con_clave = [ln for ln in lineas if (getattr(ln, "clave", None) or "").strip()]
    if not con_clave:
        return
    existentes = {
        pc.producto_id: pc
        for pc in db.query(ProductoCliente)
        .filter(ProductoCliente.cliente_id == oc.cliente_id)
        .all()
    }
    for ln in con_clave:
        if ln.producto_id in existentes:
            continue
        db.add(ProductoCliente(
            tenant_id=ctx.tenant_id, cliente_id=oc.cliente_id,
            producto_id=ln.producto_id,
            codigo_cliente=ln.clave.strip()[:50],
            nombre_cliente=(ln.texto_original or "").strip()[:254] or None,
            presentacion=ln.presentacion,
        ))
        existentes[ln.producto_id] = True  # una sola fila por producto en este lote
    db.flush()


@router.post("/{oc_id}/crear-remision-auto", response_model=OCRecibidaDetailOut)
def crear_remision_auto(
    oc_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
    almacen_id: Optional[UUID] = Query(default=None),
):
    """Un clic: la OC que cruzó COMPLETA por vías deterministas se vuelve remisión.

    La evaluación se recalcula en este momento (una guardada hace horas miente:
    el catálogo y las listas cambian) y si algo dejó de cruzar responde 409 con
    el motivo — el folio no se quema hasta que todo está resuelto. Las líneas
    salen con el precio de la lista negociada que validó la evaluación.
    """
    from ...schemas.oc_recibida import CrearRemisionIn, LineaCrearIn

    oc = get_or_404(db, OCRecibida, oc_id, soft=False, for_update=True)
    det = _detalle(db, oc)
    auto = det.get("auto") or {}
    if not auto.get("ok"):
        raise HTTPException(
            status_code=409,
            detail=auto.get("motivo") or "La orden no cruza completa por vía determinista",
        )
    payload = CrearRemisionIn(
        # El almacén que eligió el operador en la bandeja. Vacío = la cadena de
        # siempre (sucursal → cliente → predeterminado).
        almacen_id=almacen_id,
        lineas=[
            LineaCrearIn(
                producto_id=UUID(l["producto_id"]),
                cantidad=Decimal(l["cantidad"]),
                presentacion=l["presentacion"],
                precio_unitario=Decimal(l["precio_unitario"]),
                texto_original=(l.get("texto_original") or None),
                clave=(l.get("clave") or None),
            )
            for l in auto["lineas"]
        ],
    )
    return crear_remision(oc_id, payload, db=db, ctx=ctx)


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
