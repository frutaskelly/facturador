"""Envíos de cobranza — cada estado de cuenta configurado por separado.

Un envío junta una razón social o varias (EHMO + SUREÑA + MAFAN) y dice a
quién se manda, cómo se acomoda la tabla, qué incluye y cuándo sale:
AUTOMATICO el día y la hora que diga; MANUAL solo con el botón Enviar. Las
reglas viven en `services/cobranza_grupos.py` (cómo se arma) y
`services/cobranza_auto.py` (cómo se manda y la bitácora).

El previo recibe el envío tal como está en el editor, guardado o no: así se
ve el correo antes de guardar. Ver pide `menu:facturas`; guardar, cambiar el
modo y mandar piden `factura:gestionar`.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import (
    Cliente, CobranzaEnvio, CobranzaGrupo, CobranzaGrupoAlcance, Factura, Proyecto, Tenant,
)
from ...services import cobranza_auto as auto
from ...services import cobranza_grupos as svc
from .cobranza import _READ, _WRITE
from .cobranza_auto import envio_out
from .remisiones import _validar_destinatarios

router = APIRouter(prefix="/cobranza/automatica/grupos", tags=["cobranza"])
ZERO = Decimal("0")


class AlcanceIn(BaseModel):
    cliente_id: UUID
    # True = la razón social completa (sus proyectos nuevos entran solos).
    completo: bool = True
    proyectos: list[UUID] = Field(default_factory=list, max_length=200)
    # Series de lo que no cae en ningún proyecto.
    series: list[str] = Field(default_factory=list, max_length=50)


class GrupoIn(BaseModel):
    # Solo para el previo de un envío ya guardado: así el aviso «también va
    # en…» no lo compara consigo mismo.
    id: Optional[UUID] = None
    nombre: str = Field(min_length=1, max_length=80)
    alcance: list[AlcanceIn] = Field(min_length=1, max_length=100)
    agrupar_por: Literal["PROYECTO", "SERIE", "SUCURSAL", "CLIENTE"] = "PROYECTO"
    mostrar_antiguedad: bool = False
    correos: list[str] = Field(default_factory=list, max_length=20)
    cc: list[str] = Field(default_factory=list, max_length=20)
    modo: Literal["AUTOMATICO", "MANUAL"] = "MANUAL"
    dia_semana: int = Field(default=0, ge=0, le=6)
    hora: int = Field(default=8, ge=0, le=23)
    incluir_por_vencer: bool = True
    saldo_minimo: Decimal = Field(default=Decimal("100"), ge=0, le=10_000_000)
    escalar_dias: int = Field(default=30, ge=0, le=365)
    escalar_cc: list[str] = Field(default_factory=list, max_length=20)
    adjuntar_pdf: bool = True
    adjuntar_excel: bool = True
    asunto: Optional[str] = Field(default=None, max_length=200)
    mensaje: Optional[str] = Field(default=None, max_length=4000)
    nota: Optional[str] = Field(default=None, max_length=254)


def _correos(lista: list[str]) -> list[str]:
    return _validar_destinatarios([x.strip() for x in lista if x.strip()])


def _definicion(db: Session, ctx: AuthContext, p: GrupoIn) -> svc.Definicion:
    """Valida lo capturado y lo vuelve una definición: clientes del inquilino
    y dentro del candado, proyectos del inquilino, correos válidos."""
    nombre = " ".join(p.nombre.split())
    if not nombre:
        raise HTTPException(status_code=422, detail="Ponle nombre al envío.")
    ids = [a.cliente_id for a in p.alcance]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail="Una razón social aparece dos veces en el envío.")
    clientes = {c.id: c for c in db.query(Cliente).filter(
        Cliente.tenant_id == ctx.tenant_id, Cliente.id.in_(ids), Cliente.deleted_at.is_(None)).all()}
    if len(clientes) != len(ids) or not all(ctx.cliente_permitido(i) for i in ids):
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    pids = {pid for a in p.alcance for pid in a.proyectos}
    if pids:
        existen = {i for (i,) in db.query(Proyecto.id).filter(
            Proyecto.tenant_id == ctx.tenant_id, Proyecto.id.in_(pids)).all()}
        if existen != pids:
            raise HTTPException(status_code=422, detail="Proyecto no encontrado")
    alcance = []
    for a in p.alcance:
        series = {s.strip().upper() for s in a.series if s.strip()}
        if any(len(s) > 10 for s in series):
            raise HTTPException(status_code=422, detail="Serie inválida")
        if not a.completo and not a.proyectos and not series:
            raise HTTPException(
                status_code=422,
                detail=f"Marca al menos un proyecto de {svc.nombre_corto(clientes[a.cliente_id])}, "
                       "o la razón social completa.")
        alcance.append(svc.Alcance(
            cliente_id=a.cliente_id, completo=a.completo,
            proyectos=set() if a.completo else set(a.proyectos),
            series=set() if a.completo else series))
    texto = lambda v: (v or "").strip() or None  # noqa: E731
    return svc.Definicion(
        id=p.id, nombre=nombre, alcance=alcance, agrupar_por=p.agrupar_por,
        mostrar_antiguedad=p.mostrar_antiguedad, correos=_correos(p.correos), cc=_correos(p.cc),
        modo=p.modo, dia_semana=p.dia_semana, hora=p.hora, incluir_por_vencer=p.incluir_por_vencer,
        saldo_minimo=p.saldo_minimo, escalar_dias=p.escalar_dias, escalar_cc=_correos(p.escalar_cc),
        adjuntar_pdf=p.adjuntar_pdf, adjuntar_excel=p.adjuntar_excel,
        asunto=texto(p.asunto), mensaje=texto(p.mensaje), nota=texto(p.nota),
    )


def _guardados(db: Session, ctx: AuthContext) -> list[tuple[CobranzaGrupo, svc.Definicion]]:
    grupos = db.query(CobranzaGrupo).filter(CobranzaGrupo.tenant_id == ctx.tenant_id).all()
    filas: dict[UUID, list[CobranzaGrupoAlcance]] = {}
    for f in db.query(CobranzaGrupoAlcance).filter(CobranzaGrupoAlcance.tenant_id == ctx.tenant_id).all():
        filas.setdefault(f.grupo_id, []).append(f)
    out = []
    for g in grupos:
        d = svc.definicion_de(db, g, filas.get(g.id, []))
        # Con candado por cliente solo se ve el envío si se ven TODAS sus razones.
        if d.alcance and all(ctx.cliente_permitido(a.cliente_id) for a in d.alcance):
            out.append((g, d))
    return out


def _tambien_en(d: svc.Definicion, otros: list[tuple[CobranzaGrupo, svc.Definicion]]) -> list[dict]:
    """Los otros envíos que cubren alguna factura de este (aviso, no error)."""
    out = []
    for g, o in otros:
        if d.id is not None and g.id == d.id:
            continue
        comunes = [a.cliente_id for a in d.alcance if any(a.choca_con(b) for b in o.alcance)]
        if comunes:
            out.append({"grupo_id": str(g.id), "nombre": g.nombre, "clientes": [str(c) for c in comunes]})
    return out


class _Lista:
    """Lo que la lista de envíos comparte: catálogo, config, la última fila de
    la bitácora de cada envío y si su automático de hoy ya se resolvió."""

    def __init__(self, db: Session, ctx: AuthContext):
        self.cx = svc.Contexto(db, ctx)
        self.cfg = auto.config_de(db, ctx.tenant_id)
        self.ahora = dt.datetime.now(dt.timezone.utc)
        hoy = auto.hoy_local(self.cfg)
        self.ultimo: dict[UUID, CobranzaEnvio] = {}
        self.salio_hoy: set[UUID] = set()
        for e in db.query(CobranzaEnvio).filter(
                CobranzaEnvio.tenant_id == ctx.tenant_id, CobranzaEnvio.grupo_id.isnot(None),
                CobranzaEnvio.corte >= hoy - dt.timedelta(days=120)
        ).order_by(CobranzaEnvio.created_at.desc()).all():
            self.ultimo.setdefault(e.grupo_id, e)
            if e.origen == "PROGRAMADO" and e.corte == hoy and e.estado in ("ENVIADO", "DESCARTADO", "ERROR"):
                self.salio_hoy.add(e.grupo_id)


def _grupo_out(g: CobranzaGrupo, d: svc.Definicion, ls: _Lista, otros) -> dict:
    datos = svc.datos_de_envio(ls.cx, d)
    ultimo = ls.ultimo.get(g.id)
    proximo = None
    if g.modo == "AUTOMATICO" and ls.cfg.activo:
        proximo = auto.proximo(g, ls.cfg, ls.ahora, g.id in ls.salio_hoy)
    return {
        "id": str(g.id),
        **{c: getattr(g, c) for c in svc.CAMPOS},
        "correos": g.correos or [], "cc": g.cc or [], "escalar_cc": g.escalar_cc or [],
        "alcance": [{
            "cliente_id": str(a.cliente_id), "cliente": svc.nombre_corto(ls.cx.cliente(a.cliente_id)),
            "completo": a.completo, "proyectos": sorted(str(p) for p in a.proyectos),
            "series": sorted(a.series),
        } for a in sorted(d.alcance, key=lambda a: ls.cx.cliente(a.cliente_id).legal_name or "")],
        "saldo": datos["saldo_total"], "vencido": datos["vencido_total"], "facturas": datos["facturas"],
        "cuando": auto.cuando_texto(g),
        "proximo": proximo,
        "ultimo": envio_out(ultimo) if ultimo else None,
        "tambien_en": _tambien_en(d, otros),
    }


def _correos_de_ficha(c: Cliente) -> list[str]:
    dom = c.domicilio_fiscal or {}
    if isinstance(dom.get("correos"), list):
        return [str(x) for x in dom["correos"] if str(x).strip()]
    return [str(dom["email"])] if dom.get("email") else []


def _sin_envio(db: Session, ctx: AuthContext, ls: _Lista, guardados) -> list[dict]:
    """Razones sociales con saldo que ningún envío cubre (o cubre solo en
    parte): nadie les está cobrando eso."""
    con_saldo = [c for (c,) in db.query(Factura.cliente_id).filter(
        Factura.tenant_id == ctx.tenant_id, Factura.deleted_at.is_(None),
        Factura.estado == "TIMBRADA", Factura.metodo_pago == "PPD",
        Factura.saldo_insoluto > 0).distinct().all() if ctx.cliente_permitido(c)]
    alcances: dict[UUID, list[svc.Alcance]] = {}
    for _g, d in guardados:
        for a in d.alcance:
            alcances.setdefault(a.cliente_id, []).append(a)
    out = []
    for cid in con_saldo:
        docs = [doc for doc in ls.cx.estado(cid)["facturas"]
                if not any(a.entra(doc) for a in alcances.get(cid, []))]
        if not docs:
            continue
        c = ls.cx.cliente(cid)
        out.append({
            "cliente_id": str(cid), "nombre": svc.nombre_corto(c), "legal_name": c.legal_name,
            "saldo": sum((Decimal(x["saldo_insoluto"]) for x in docs), ZERO), "facturas": len(docs),
            "parcial": cid in alcances, "correos": _correos_de_ficha(c),
        })
    return sorted(out, key=lambda x: x["saldo"], reverse=True)


@router.get("")
def listar(db: Session = Depends(get_tenant_db),
           ctx: AuthContext = Depends(require_permission(_READ))):
    """Los envíos con su saldo de hoy, cuándo salen, su último correo y con
    quién comparten razones sociales; más las razones sin envío."""
    guardados = _guardados(db, ctx)
    ls = _Lista(db, ctx)
    envios = sorted((_grupo_out(g, d, ls, guardados) for g, d in guardados), key=lambda x: x["nombre"].lower())
    return {"envios": envios, "sin_envio": _sin_envio(db, ctx, ls, guardados)}


@router.get("/opciones")
def opciones(db: Session = Depends(get_tenant_db),
             ctx: AuthContext = Depends(require_permission(_READ))):
    """El árbol «qué incluye»: cada razón social con sus proyectos (con saldo,
    o del catálogo aunque hoy no deban nada) y las series que no caen en
    ningún proyecto, con su saldo de hoy."""
    cx = svc.Contexto(db, ctx)
    con_saldo = {c for (c,) in db.query(Factura.cliente_id).filter(
        Factura.tenant_id == ctx.tenant_id, Factura.deleted_at.is_(None),
        Factura.estado == "TIMBRADA", Factura.metodo_pago == "PPD",
        Factura.saldo_insoluto > 0).distinct().all()}
    clientes = db.query(Cliente).filter(
        Cliente.tenant_id == ctx.tenant_id, Cliente.deleted_at.is_(None),
        sa.or_(Cliente.status == "ACTIVO", Cliente.id.in_(con_saldo or [None]))
    ).order_by(Cliente.legal_name).all()
    catalogo: dict[UUID, list[Proyecto]] = {}
    for p in db.query(Proyecto).filter(Proyecto.tenant_id == ctx.tenant_id, Proyecto.deleted_at.is_(None),
                                       Proyecto.reporta_en_id.is_(None), Proyecto.cliente_id.isnot(None)).all():
        catalogo.setdefault(p.cliente_id, []).append(p)

    out = []
    for c in clientes:
        if not ctx.cliente_permitido(c.id):
            continue
        nodos: dict[str, dict] = {}
        for p in catalogo.get(c.id, []):
            nodos[str(p.id)] = {"proyecto_id": str(p.id), "proyecto": p.nombre, "serie": None,
                                "series": list(p.series or []), "sucursal": cx.sucursales.get(p.sucursal_id),
                                "saldo": ZERO, "facturas": 0}
        if c.id in con_saldo:
            for doc in cx.estado(c.id)["facturas"]:
                if doc.get("proyecto_id"):
                    clave = doc["proyecto_id"]
                    if clave not in nodos:
                        p = cx.clasificador.por_id(UUID(clave))
                        nodos[clave] = {"proyecto_id": clave, "proyecto": doc["proyecto"], "serie": None,
                                        "series": list(p.series or []) if p else [],
                                        "sucursal": cx.sucursales.get(p.sucursal_id) if p else None,
                                        "saldo": ZERO, "facturas": 0}
                else:
                    serie = doc.get("serie") or ""
                    clave = f"serie:{serie}"
                    if clave not in nodos:
                        suc = cx.sucursal_de(c.id, doc)
                        nodos[clave] = {"proyecto_id": None, "proyecto": None, "serie": serie, "series": [serie],
                                        "sucursal": cx.sucursales.get(suc) if suc else None,
                                        "saldo": ZERO, "facturas": 0}
                nodos[clave]["saldo"] += Decimal(doc["saldo_insoluto"])
                nodos[clave]["facturas"] += 1
        lista = sorted(nodos.values(), key=lambda n: (n["proyecto"] is None, n["proyecto"] or n["serie"] or ""))
        out.append({"cliente_id": str(c.id), "nombre": svc.nombre_corto(c), "legal_name": c.legal_name,
                    "codigo": c.codigo, "correos": _correos_de_ficha(c),
                    "saldo": sum((n["saldo"] for n in lista), ZERO), "nodos": lista})
    return out


def _guardar(db: Session, ctx: AuthContext, g: CobranzaGrupo, d: svc.Definicion) -> None:
    for c in svc.CAMPOS:
        setattr(g, c, getattr(d, c))
    g.correos, g.cc, g.escalar_cc = d.correos, d.cc, d.escalar_cc
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya hay un envío con ese nombre.")
    db.query(CobranzaGrupoAlcance).filter(CobranzaGrupoAlcance.tenant_id == ctx.tenant_id,
                                          CobranzaGrupoAlcance.grupo_id == g.id).delete()
    db.add_all(svc.filas_alcance(ctx.tenant_id, g.id, d.alcance))
    db.commit()


def _respuesta(db: Session, ctx: AuthContext, grupo_id: UUID) -> dict:
    guardados = _guardados(db, ctx)
    ls = _Lista(db, ctx)
    for g, d in guardados:
        if g.id == grupo_id:
            return _grupo_out(g, d, ls, guardados)
    raise HTTPException(status_code=404, detail="Envío no encontrado")


@router.post("", status_code=201)
def crear(payload: GrupoIn, db: Session = Depends(get_tenant_db),
          ctx: AuthContext = Depends(require_permission(_WRITE))):
    d = _definicion(db, ctx, payload)
    g = CobranzaGrupo(tenant_id=ctx.tenant_id, nombre=d.nombre)
    db.add(g)
    _guardar(db, ctx, g, d)
    return _respuesta(db, ctx, g.id)


def _propio(db: Session, ctx: AuthContext, grupo_id: UUID) -> CobranzaGrupo:
    g = db.query(CobranzaGrupo).filter(CobranzaGrupo.id == grupo_id,
                                       CobranzaGrupo.tenant_id == ctx.tenant_id).one_or_none()
    if g is None or not any(x.id == grupo_id for x, _ in _guardados(db, ctx)):
        raise HTTPException(status_code=404, detail="Envío no encontrado")
    return g


@router.put("/{grupo_id}")
def cambiar(grupo_id: UUID, payload: GrupoIn, db: Session = Depends(get_tenant_db),
            ctx: AuthContext = Depends(require_permission(_WRITE))):
    g = _propio(db, ctx, grupo_id)
    d = _definicion(db, ctx, payload)
    _guardar(db, ctx, g, d)
    return _respuesta(db, ctx, g.id)


class ModoIn(BaseModel):
    modo: Literal["AUTOMATICO", "MANUAL"]


@router.patch("/{grupo_id}/modo")
def cambiar_modo(grupo_id: UUID, payload: ModoIn, db: Session = Depends(get_tenant_db),
                 ctx: AuthContext = Depends(require_permission(_WRITE))):
    """El interruptor Automático / Manual de la lista, con un clic."""
    g = _propio(db, ctx, grupo_id)
    g.modo = payload.modo
    db.commit()
    return _respuesta(db, ctx, g.id)


@router.delete("/{grupo_id}")
def borrar(grupo_id: UUID, db: Session = Depends(get_tenant_db),
           ctx: AuthContext = Depends(require_permission(_WRITE))):
    db.delete(_propio(db, ctx, grupo_id))
    db.commit()
    return {"ok": True}


# ─── Mandar con un clic ──────────────────────────────────────────────────────

@router.post("/{grupo_id}/enviar")
def enviar(grupo_id: UUID, db: Session = Depends(get_tenant_db),
           ctx: AuthContext = Depends(require_permission(_WRITE))):
    """Manda el estado de cuenta ahora mismo. Si algo lo impide (espejo viejo,
    sin correo, sin saldo) responde 409 con el motivo y no deja rastro; si el
    SMTP lo rechaza queda en la bitácora como ERROR."""
    g = _propio(db, ctx, grupo_id)
    try:
        e = auto.enviar_grupo(db, ctx.tenant_id, g, origen="MANUAL", usuario=ctx.user_id)
    except auto.NoSeEnvia as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return {"envio": envio_out(e), "grupo": _respuesta(db, ctx, g.id)}


class IdsIn(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=200)


@router.post("/enviar")
def enviar_varios(payload: IdsIn, db: Session = Depends(get_tenant_db),
                  ctx: AuthContext = Depends(require_permission(_WRITE))):
    """Manda varios de una vez. Cada uno se confirma por separado: si el 10
    falla, los 9 que ya salieron quedan como enviados."""
    propios = {g.id: g for g, _ in _guardados(db, ctx)}
    resultado = []
    for i in payload.ids:
        g = propios.get(i)
        if g is None:
            resultado.append({"grupo_id": str(i), "nombre": "—", "estado": "ERROR", "error": "Envío no encontrado"})
            continue
        try:
            e = auto.enviar_grupo(db, ctx.tenant_id, g, origen="MANUAL", usuario=ctx.user_id)
            db.commit()
            resultado.append({"grupo_id": str(i), "nombre": g.nombre, "estado": e.estado, "error": e.error})
        except auto.NoSeEnvia as exc:
            db.rollback()
            resultado.append({"grupo_id": str(i), "nombre": g.nombre, "estado": "NO_SALIO", "error": str(exc)})
    return resultado


# ─── Previo, archivos y prueba ───────────────────────────────────────────────

@router.post("/previo")
def previo(payload: GrupoIn, db: Session = Depends(get_tenant_db),
           ctx: AuthContext = Depends(require_permission(_READ))):
    """El correo tal como saldría hoy, con lo que trae el editor (guardado o no)."""
    d = _definicion(db, ctx, payload)
    cfg = auto.config_de(db, ctx.tenant_id)
    out = svc.previo(svc.Contexto(db, ctx), cfg, d)
    for t in _tambien_en(d, _guardados(db, ctx)):
        out["avisos"].append(f"También va en el envío «{t['nombre']}»: esas facturas se cobrarían en los dos correos.")
    return out


def _archivo(db: Session, ctx: AuthContext, payload: GrupoIn, tipo: str) -> Response:
    d = _definicion(db, ctx, payload)
    datos = svc.datos_de_envio(svc.Contexto(db, ctx), d)
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    nombre = svc.nombre_archivo(datos)
    if tipo == "pdf":
        return Response(content=svc.pdf(tenant, datos), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{nombre}.pdf"'})
    return Response(content=svc.xlsx(tenant, datos),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}.xlsx"'})


@router.post("/previo/xlsx")
def previo_xlsx(payload: GrupoIn, db: Session = Depends(get_tenant_db),
                ctx: AuthContext = Depends(require_permission(_READ))):
    """El Excel que va adjunto: hoja Resumen + una hoja por fila."""
    return _archivo(db, ctx, payload, "xlsx")


@router.post("/previo/pdf")
def previo_pdf(payload: GrupoIn, db: Session = Depends(get_tenant_db),
               ctx: AuthContext = Depends(require_permission(_READ))):
    """El PDF que va adjunto: resumen y detalle de cada fila."""
    return _archivo(db, ctx, payload, "pdf")


@router.post("/prueba")
def prueba(payload: GrupoIn, db: Session = Depends(get_tenant_db),
           ctx: AuthContext = Depends(require_permission(_WRITE))):
    """Manda el correo SOLO a quien lo pide (su correo de usuario), con una
    franja arriba que dice a quién iría de verdad. Nunca a los del envío."""
    import html as html_mod

    from ...services import email as email_service

    if not ctx.email:
        raise HTTPException(status_code=400, detail="Tu usuario no tiene correo: no hay a dónde mandar la prueba.")
    d = _definicion(db, ctx, payload)
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    if not email_service.configured(tenant):
        raise HTTPException(status_code=503, detail="Configura una cuenta de correo en Ajustes › Correo")
    cfg = auto.config_de(db, ctx.tenant_id)
    datos = svc.datos_de_envio(svc.Contexto(db, ctx), d)
    para, cc, _escalado = svc.destinatarios(cfg, d, datos["dias_max_vencida"])
    destino = ", ".join(para) or "(falta capturar el correo)"
    if cc:
        destino += f" · cc {', '.join(cc)}"
    franja = ("<p style=\"background:#fff4dc;color:#8a5a00;padding:8px 12px;border-radius:6px;font-size:12px\">"
              f"Correo de prueba: de verdad iría a {html_mod.escape(destino)}. Solo te llegó a ti.</p>")
    try:
        email_service.send_email(email_service.smtp_config(tenant), [ctx.email],
                                 f"[Prueba] {svc.asunto(d, datos)}", svc.html_correo(d, datos, encabezado=franja),
                                 attachments=svc.adjuntos(tenant, d, datos))
    except Exception as exc:  # noqa: BLE001 — el motivo del SMTP se muestra tal cual
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "to": ctx.email}
