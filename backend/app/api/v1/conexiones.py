"""Conexiones — enchufar un sistema externo sin repartir contraseñas.

La pantalla que sirve este router responde dos cosas y nada más: cómo conectar
Smart Supply (o Mini Conta), y si está entrando lo que debe. Por eso el estado no devuelve
configuración sino ACTIVIDAD (última orden, cuántas hoy, cuántas sin resolver):
una vez conectado, eso es lo único que alguien va a venir a mirar.

Smart Supply tiene UNA clave por empresa. Mini Conta tiene una por CUENTA (cada
cuenta de Mini Conta es un cliente aparte), con nombre y alcance: qué series,
qué clientes y si comparte el catálogo. Generar o revocar la de una cuenta no
toca a las demás.

Gestionar conexiones es cosa del dueño o de quien administre la empresa, así que
se reusa `membership:gestionar` — el mismo permiso que abre Empresa y Correo.
Leerlas pide `menu:ajustes.usuarios` no: basta con poder gestionar.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_auth_context, get_tenant_db, require_permission
from ...models import (
    Almacen,
    Cliente,
    ClienteExterno,
    Conexion,
    GrupoWhatsapp,
    OCRecibida,
    Serie,
    Sucursal,
    Tenant,
)
from ...models.conexion import generar_clave, hash_clave, pista_de
from ...schemas.conexion import (
    ActividadConexionOut,
    ClaveNuevaOut,
    ClienteDelGrupoOut,
    SucursalBreve,
    ConexionEstadoOut,
    ConexionOut,
    ConexionUpdate,
    GrupoOut,
    GrupoUpdate,
    NuevaConexionIn,
    OpcionesMiniContaOut,
    PruebaOut,
    SincronizarGruposIn,
)
from ...services import cliente_match
from ...services.mini_conta import Alcance, mapa, validar_alcance
from ._helpers import get_or_404

router = APIRouter(prefix="/conexiones", tags=["conexiones"])

_GESTIONAR = "membership:gestionar"

# El catálogo vive aquí para que la pantalla pueda listar lo que se puede
# conectar sin conocer nada más. El ALCANCE de cada uno está en
# core/rbac.py::PERMISOS_POR_TIPO.
CATALOGO = {
    "SMART_SUPPLY": {
        "nombre": "Smart Supply",
        "descripcion": "Órdenes de compra por WhatsApp",
    },
    "MINI_CONTA": {
        "nombre": "Mini Conta",
        "descripcion": "Lee las ventas facturadas por sucursal",
    },
}

# Sin vencimiento (decisión del dueño): una clave que caduca sola se cae de
# madrugada. En vez de eso, al año la pantalla sugiere rotarla.
_DIAS_PARA_SUGERIR_ROTAR = 365


# Tipos con una clave por cuenta (varias vivas a la vez). El resto: una por empresa.
_POR_CUENTA = {"MINI_CONTA"}


def _vivas(db: Session, tipo: str) -> list[Conexion]:
    return (
        db.query(Conexion)
        .filter(Conexion.tipo == tipo, Conexion.estado != "REVOCADA")
        .order_by(Conexion.created_at.desc())
        .all()
    )


def _viva(db: Session, tipo: str) -> Optional[Conexion]:
    vivas = _vivas(db, tipo)
    return vivas[0] if vivas else None


def _estado(db: Session, tipo: str) -> ConexionEstadoOut:
    meta = CATALOGO[tipo]
    vivas = _vivas(db, tipo)
    out = ConexionEstadoOut(tipo=tipo, nombre=meta["nombre"])
    if not vivas:
        return out

    con = vivas[0]
    out.conexion = ConexionOut.model_validate(con)
    out.conexiones = [
        ConexionOut.model_validate(c)
        for c in sorted(vivas, key=lambda c: (c.nombre or "").casefold())
    ]
    creada = con.created_at
    if creada is not None:
        if creada.tzinfo is None:
            creada = creada.replace(tzinfo=timezone.utc)
        dias = (datetime.now(timezone.utc) - creada).days
        out.dias_desde_creacion = dias
        out.conviene_rotar = dias >= _DIAS_PARA_SUGERIR_ROTAR

    if tipo != "SMART_SUPPLY":
        # La actividad de la bandeja es de Smart Supply; Mini Conta solo lee,
        # y lo que la pantalla muestra de ella es `conexion.ultimo_uso_at`.
        return out

    desde = datetime.now(timezone.utc) - timedelta(hours=24)
    out.ordenes_hoy = (
        db.query(func.count(OCRecibida.id))
        .filter(OCRecibida.recibida_at >= desde)
        .scalar()
        or 0
    )
    out.ordenes_sin_resolver = (
        db.query(func.count(OCRecibida.id))
        .filter(OCRecibida.estado == "PENDIENTE", OCRecibida.cliente_id.is_(None))
        .scalar()
        or 0
    )
    out.ultima_orden_at = db.query(func.max(OCRecibida.recibida_at)).scalar()
    return out


@router.get("", response_model=list[ConexionEstadoOut])
def listar(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_GESTIONAR)),
):
    """Todo lo conectable, conectado o no. La pantalla no necesita otra llamada."""
    return [_estado(db, tipo) for tipo in CATALOGO]


def _nombre_libre(db: Session, nombre: str, excepto=None) -> None:
    q = db.query(Conexion).filter(
        Conexion.tipo == "MINI_CONTA",
        Conexion.estado != "REVOCADA",
        func.lower(Conexion.nombre) == nombre.strip().lower(),
    )
    if excepto is not None:
        q = q.filter(Conexion.id != excepto)
    if q.first() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Ya hay una conexión «{nombre.strip()}»: genera su clave nueva desde ella",
        )


def _clave_nueva(db: Session, ctx: AuthContext, tipo: str, nombre: str,
                 alcance: Optional[dict]) -> ClaveNuevaOut:
    clave = generar_clave()
    con = Conexion(
        tenant_id=ctx.tenant_id,
        tipo=tipo,
        nombre=nombre,
        clave_hash=hash_clave(clave),
        clave_pista=pista_de(clave),
        estado="PENDIENTE",
        created_by=ctx.user_id,
        alcance=alcance,
    )
    db.add(con)
    db.flush()
    db.refresh(con)
    return ClaveNuevaOut(
        clave=clave,
        conexion=ConexionOut.model_validate(con),
        instruccion_whatsapp=(
            f"smart supply: conectar facturador {clave}" if tipo == "SMART_SUPPLY" else None
        ),
    )


def _revocar(con: Conexion) -> None:
    con.estado = "REVOCADA"
    con.revocada_at = datetime.now(timezone.utc)


@router.post("/{tipo}/clave", response_model=ClaveNuevaOut, status_code=status.HTTP_201_CREATED)
def generar(
    tipo: str,
    payload: Optional[NuevaConexionIn] = Body(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_GESTIONAR)),
):
    """Genera la clave. Se devuelve en claro UNA vez y ya no se puede volver a leer.

    Smart Supply: si ya había una viva se revoca en el acto —«generar otra» y «la
    anterior deja de servir» tienen que ser el mismo gesto, o quedarían dos claves
    buenas y nadie sabría cuál está usando el bot—.

    Mini Conta: cada llamada es una cuenta NUEVA (nombre + alcance) y no toca a
    las demás. Para cambiar la clave de una cuenta existente: `/{id}/regenerar`.
    """
    tipo = tipo.upper()
    if tipo not in CATALOGO:
        raise HTTPException(status_code=404, detail="Ese sistema no se puede conectar")

    if tipo in _POR_CUENTA:
        if payload is None:
            raise HTTPException(
                status_code=422,
                detail="Di de qué cuenta es la clave y qué comparte (nombre y alcance)",
            )
        nombre = payload.nombre.strip()
        _nombre_libre(db, nombre)
        a = payload.alcance
        alcance = validar_alcance(db, ctx.tenant_id, a.series, a.clientes, a.catalogo)
        return _clave_nueva(db, ctx, tipo, nombre, alcance)

    anterior = _viva(db, tipo)
    if anterior is not None:
        _revocar(anterior)
        db.flush()
    return _clave_nueva(db, ctx, tipo, CATALOGO[tipo]["nombre"], None)


@router.post("/{conexion_id}/regenerar", response_model=ClaveNuevaOut,
             status_code=status.HTTP_201_CREATED)
def regenerar(
    conexion_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_GESTIONAR)),
):
    """Clave nueva para ESA conexión: la anterior deja de servir en el acto y la
    nueva hereda su nombre y su alcance. Las demás cuentas no se enteran."""
    con = get_or_404(db, Conexion, conexion_id, soft=False)
    if con.estado == "REVOCADA":
        raise HTTPException(status_code=409, detail="Esa conexión ya estaba desconectada")
    _revocar(con)
    db.flush()
    return _clave_nueva(db, ctx, con.tipo, con.nombre, con.alcance)


@router.patch("/{conexion_id}", response_model=ConexionOut)
def editar(
    conexion_id: UUID,
    payload: ConexionUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_GESTIONAR)),
):
    """Cambia el nombre o lo que comparte una conexión de Mini Conta, sin tocar
    su clave: Mini Conta ve el cambio la próxima vez que lea."""
    con = get_or_404(db, Conexion, conexion_id, soft=False)
    if con.tipo not in _POR_CUENTA:
        raise HTTPException(status_code=422, detail="Esta conexión no tiene alcance que editar")
    if con.estado == "REVOCADA":
        raise HTTPException(status_code=409, detail="Esa conexión ya estaba desconectada")
    if payload.nombre is not None:
        _nombre_libre(db, payload.nombre, excepto=con.id)
        con.nombre = payload.nombre.strip()
    if payload.alcance is not None:
        a = payload.alcance
        con.alcance = validar_alcance(db, ctx.tenant_id, a.series, a.clientes, a.catalogo)
    db.flush()
    db.refresh(con)
    return con


@router.get("/{tipo}/opciones", response_model=OpcionesMiniContaOut)
def opciones(
    tipo: str,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_GESTIONAR)),
):
    """Lo que se le puede compartir a una cuenta de Mini Conta: plazas con sus
    series y los clientes de cada serie."""
    if tipo.upper() not in _POR_CUENTA:
        raise HTTPException(status_code=404, detail="Esta conexión no tiene alcance que escoger")
    m = mapa(db, ctx.tenant_id, Alcance.todo())
    return OpcionesMiniContaOut(
        sucursales=m["sucursales"], series=m["series"], clientes=m["clientes"],
    )


@router.post("/{conexion_id}/revocar", response_model=ConexionOut)
def revocar(
    conexion_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_GESTIONAR)),
):
    con = get_or_404(db, Conexion, conexion_id, soft=False)
    if con.estado == "REVOCADA":
        raise HTTPException(status_code=409, detail="Esa conexión ya estaba desconectada")
    _revocar(con)
    db.flush()
    db.refresh(con)
    return con


@router.get("/{tipo}/actividad", response_model=list[ActividadConexionOut])
def actividad(
    tipo: str,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_GESTIONAR)),
):
    """Las últimas órdenes que entraron. Es la prueba de que sirve, no un log."""
    if tipo.upper() not in CATALOGO:
        raise HTTPException(status_code=404, detail="Ese sistema no se puede conectar")
    filas = (
        db.query(OCRecibida)
        .order_by(OCRecibida.recibida_at.desc())
        .limit(8)
        .all()
    )
    return [
        ActividadConexionOut(
            recibida_at=f.recibida_at,
            folio_externo=f.folio_externo,
            remitente=f.remitente,
            cliente_nombre=f.cliente_nombre,
            estado=f.estado if f.cliente_id else "SIN_CLIENTE",
            partidas=len((f.payload or {}).get("lineas") or []),
        )
        for f in filas
    ]


@router.get("/probar", response_model=PruebaOut)
def probar(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Confirma que una clave sirve, sin escribir nada.

    Lo llama el bot al conectarse (para poder responder «listo» en el chat), Mini
    Conta al guardar la clave, y la pantalla con el botón «Probar conexión».
    Cualquier clave viva pasa —el alcance cambia por tipo y no todas tienen
    `menu:remisiones`—; a una persona se le sigue pidiendo ese permiso.
    """
    if ctx.conexion_id is None and not ctx.has("menu:remisiones"):
        raise HTTPException(status_code=403, detail="Falta permiso: menu:remisiones")
    if ctx.cliente_scope:
        # Un usuario con candado por cliente (portal) no toca la conexión del bot.
        raise HTTPException(status_code=403, detail="Tu usuario no administra conexiones")

    t = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one_or_none()
    if ctx.conexion_id is not None:
        return PruebaOut(
            ok=True,
            mensaje="Clave válida. Las órdenes que mandes aparecerán en la bandeja.",
            tenant=t.legal_name if t else None,
            permisos=sorted(ctx.permissions),
        )
    return PruebaOut(
        ok=True,
        mensaje="Sesión válida (no es una clave de conexión).",
        tenant=t.legal_name if t else None,
        permisos=sorted(ctx.permissions),
    )


# ─── Directorio de grupos ────────────────────────────────────────────────────


@router.post("/grupos", response_model=dict)
def sincronizar_grupos(
    payload: SincronizarGruposIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("menu:remisiones")),
):
    """El bot reporta su directorio. Lo llama con su propia clave de conexión.

    Es un espejo, no la fuente: la verdad sigue viviendo en la config del bot.
    Los grupos que dejaron de reportarse NO se borran —se marcan inactivos— para
    no perder el historial de las órdenes que ya entraron por ahí.
    """
    if ctx.cliente_scope:
        # Un usuario con candado por cliente (portal) no toca la conexión del bot.
        raise HTTPException(status_code=403, detail="Tu usuario no administra conexiones")
    vistos = set()
    for g in payload.grupos:
        vistos.add(g.jid)
        row = (
            db.query(GrupoWhatsapp)
            .filter(GrupoWhatsapp.jid == g.jid)
            .one_or_none()
        )
        nuevo = row is None
        if nuevo:
            row = GrupoWhatsapp(tenant_id=ctx.tenant_id, jid=g.jid)
            db.add(row)
        row.nombre = g.nombre
        row.rol = (g.rol or "interno").lower()
        row.perfil = (g.perfil or "").lower() or None
        row.reportado_activo = g.activo
        # La decisión del dueño MANDA: solo se hereda la del bot al darlo de alta.
        # Si no, apagar un grupo aquí duraba hasta la siguiente sincronización.
        if nuevo:
            row.activo = g.activo
        row.config = g.config or {}
        row.sincronizado_at = datetime.now(timezone.utc)
    if vistos:
        # Un grupo que el bot dejó de reportar se marca como no reportado; su
        # `activo` sigue siendo del dueño.
        (
            db.query(GrupoWhatsapp)
            .filter(GrupoWhatsapp.jid.notin_(vistos))
            .update({GrupoWhatsapp.reportado_activo: False}, synchronize_session=False)
        )
    db.flush()
    return {"ok": True, "grupos": len(vistos)}


@router.get("/grupos", response_model=list[GrupoOut])
def listar_grupos(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_GESTIONAR)),
):
    """El mapa de qué grupo alimenta a qué: cliente, sucursales, series y qué ha
    entrado por ahí. Es la pregunta que la pantalla de Conexiones existe para
    responder una vez que el bot ya está conectado."""
    # Los activos SIEMPRE arriba (pedido del dueño): es la lista que se opera.
    grupos = (
        db.query(GrupoWhatsapp)
        .order_by(GrupoWhatsapp.activo.desc(), GrupoWhatsapp.nombre)
        .all()
    )

    # Todo en tres consultas, no una por grupo.
    externos = (
        db.query(ClienteExterno)
        .filter(ClienteExterno.sistema == "WHATSAPP")
        .all()
    )
    por_jid: dict[str, dict] = {}
    for e in externos:
        por_jid.setdefault(e.clave_normalizada, {})[e.cliente_id] = e

    clientes = {
        c.id: c
        for c in db.query(Cliente).filter(Cliente.deleted_at.is_(None)).all()
    }
    series = {s.id: s.codigo for s in db.query(Serie).all()}
    almacenes = {a.id: a.nombre for a in db.query(Almacen).filter(Almacen.deleted_at.is_(None)).all()}
    sucs: dict = {}
    for s in db.query(Sucursal).filter(Sucursal.deleted_at.is_(None)).all():
        sucs.setdefault(s.cliente_id, []).append(s)

    # Lo que REALMENTE ha entrado por cada grupo, que puede diferir de lo
    # configurado — y esa diferencia es justo lo que hay que poder ver.
    # Agregado en SQL: materializar TODAS las OCs (con sus JSONB) para contar
    # por jid crecía sin tope con la tabla.
    stats: dict = {}
    desde = datetime.now(timezone.utc) - timedelta(hours=24)
    jid_col = OCRecibida.payload["jid"].astext
    for fila in (
        db.query(
            jid_col.label("jid"),
            func.count().label("n"),
            func.count().filter(OCRecibida.recibida_at >= desde).label("n24"),
            func.max(OCRecibida.recibida_at).label("ultima"),
            func.count().filter(OCRecibida.cliente_id.is_(None)).label("pend"),
        )
        .filter(jid_col.isnot(None), jid_col != "")
        .group_by(jid_col)
        .all()
    ):
        stats[fila.jid] = {
            "n": fila.n, "n24": fila.n24, "ultima": fila.ultima,
            "pend": fila.pend, "clientes": set(),
        }
    for jid, cid in (
        db.query(jid_col, OCRecibida.cliente_id)
        .filter(jid_col.isnot(None), jid_col != "", OCRecibida.cliente_id.isnot(None))
        .distinct()
        .all()
    ):
        stats.setdefault(
            jid, {"n": 0, "n24": 0, "ultima": None, "pend": 0, "clientes": set()}
        )["clientes"].add(cid)

    salida: list[GrupoOut] = []
    for g in grupos:
        st = stats.get(g.jid, {})
        reg = por_jid.get(cliente_match.normalizar_clave("WHATSAPP", g.jid), {})
        registrados = set(reg)
        # Los que además han recibido órdenes de ahí sin estar registrados: se
        # muestran igual, marcados, porque son una inconsistencia que conviene ver.
        todos = list(registrados | set(st.get("clientes") or set()))
        filas = []
        for cid in todos:
            c = clientes.get(cid)
            if c is None:
                continue
            ext = reg.get(cid)
            filas.append(ClienteDelGrupoOut(
                externo_id=ext.id if ext else None,
                sucursal_grupo_id=ext.sucursal_id if ext else None,
                serie_factura_grupo_id=ext.serie_factura_id if ext else None,
                serie_remision_grupo_id=ext.serie_remision_id if ext else None,
                cliente_id=c.id,
                nombre=c.legal_name,
                serie_factura=series.get(c.serie_factura_id),
                serie_remision=series.get(c.serie_remision_id),
                serie_factura_id=c.serie_factura_id,
                serie_remision_id=c.serie_remision_id,
                sucursales=[
                    SucursalBreve(id=s.id, nombre=s.nombre)
                    for s in sorted(sucs.get(c.id, []), key=lambda x: x.nombre or "")
                ],
                almacen=almacenes.get(c.almacen_id),
                almacen_id=c.almacen_id,
                registrado=cid in registrados,
            ))
        filas.sort(key=lambda f: f.nombre)
        salida.append(GrupoOut(
            jid=g.jid, nombre=g.nombre, rol=g.rol, perfil=g.perfil,
            activo=g.activo, reportado_activo=g.reportado_activo,
            clientes=filas,
            ordenes=st.get("n", 0),
            ordenes_24h=st.get("n24", 0),
            ultima_orden_at=st.get("ultima"),
            sin_resolver=st.get("pend", 0),
            sincronizado_at=g.sincronizado_at,
        ))
    return salida


@router.patch("/grupos/{jid}", response_model=GrupoOut)
def actualizar_grupo(
    jid: str,
    payload: GrupoUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_GESTIONAR)),
):
    """Prender o apagar un grupo DESDE AQUÍ.

    Apagarlo no toca a Smart Supply: allá la orden se sigue procesando y entrando
    al Master. Lo que deja de hacer es ensuciar la bandeja del Facturador.
    """
    g = db.query(GrupoWhatsapp).filter(GrupoWhatsapp.jid == jid).one_or_none()
    if g is None:
        raise HTTPException(status_code=404, detail="Ese grupo no está registrado")
    g.activo = payload.activo
    db.flush()
    return next(x for x in listar_grupos(db=db, ctx=ctx) if x.jid == jid)
