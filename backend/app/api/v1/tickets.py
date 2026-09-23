"""Buzón de tickets — lo que el bot no resolvió solo, resoluble desde aquí.

Dos consumidores:
  * el BOT (clave de conexión): deposita cada ticket (POST, idempotente),
    reclama las acciones que alguien pidió desde la pantalla y confirma cómo
    le fue (acciones/pendientes → /ack).
  * la PANTALLA /tickets: lista, detalle con foto y bitácora, pedir una acción,
    comentar.

La acción la EJECUTA el bot porque es él quien tiene la foto original y la
fila de proceso (un reproceso fuera de esa fila puede correr dos visiones en
paralelo sobre el mismo pedido). Aquí solo se pide y se lleva el rastro.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Ticket
from ...schemas.common import Page
from ...schemas.ticket import (
    TicketAccionIn,
    TicketAccionPendienteOut,
    TicketAckIn,
    TicketComentarioIn,
    TicketDetailOut,
    TicketIn,
    TicketOut,
    TicketResumenOut,
)
from ._helpers import get_or_404, paginate

router = APIRouter(prefix="/tickets", tags=["tickets"])

_READ = "menu:tickets"
_WRITE = "ticket:gestionar"
_TERMINALES = ("RESUELTO", "CERRADO")


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _quien(ctx: AuthContext) -> str:
    return ctx.email or ("WhatsApp" if ctx.conexion_id else "Facturador")


def _evento(t: Ticket, quien: str, texto: str) -> None:
    t.eventos = [*(t.eventos or []), {"ts": _ahora().isoformat(), "quien": quien, "texto": texto}]
    flag_modified(t, "eventos")


def _foto(payload: TicketIn) -> Optional[bytes]:
    if not payload.foto_b64:
        return None
    try:
        return base64.b64decode(payload.foto_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="foto_b64 no es base64 válido")


def _aplicar(t: Ticket, payload: TicketIn, quien: str) -> None:
    """Vuelca lo que manda el bot sin deshacer decisiones de una persona.

    Terminal es terminal: un ABIERTO que llega tarde (reintento de red) no
    revive un ticket que alguien ya cerró. Y un EN_CURSO se sostiene mientras
    el bot no haya tomado la acción pedida: si llega ABIERTO DESPUÉS de tomarla
    es que la intentó y el caso sigue sin salir, y eso sí se refleja.
    """
    for campo in ("perfil", "grupo", "jid", "remitente", "archivo_nombre", "nota", "tipo", "que_paso"):
        valor = getattr(payload, campo)
        if valor is not None:
            setattr(t, campo, valor)
    if payload.acciones:
        t.acciones = list(payload.acciones)
    foto = _foto(payload)
    if foto is not None:
        t.foto = foto
        t.foto_mime = payload.foto_mime or "image/jpeg"

    nuevo = payload.estado
    if t.estado in _TERMINALES:
        pass
    elif nuevo in _TERMINALES:
        t.estado = nuevo
        t.resolucion = payload.resolucion or t.resolucion
        t.resuelto_at = _ahora()
        t.resuelto_por = payload.resuelto_por or quien
        if t.accion_pedida and t.accion_tomada_at is None:
            t.accion_tomada_at = _ahora()   # se resolvió por otro lado: ya no hay que ejecutarla
        _evento(t, payload.resuelto_por or quien,
                f"{'Resuelto' if nuevo == 'RESUELTO' else 'Cerrado'}"
                f"{f': {payload.resolucion}' if payload.resolucion else ''}")
    elif t.estado == "EN_CURSO" and t.accion_tomada_at is None:
        pass
    else:
        t.estado = "ABIERTO"
    if payload.evento:
        _evento(t, quien, payload.evento)


@router.post("", response_model=TicketDetailOut, status_code=status.HTTP_201_CREATED)
def depositar(
    payload: TicketIn,
    response: Response,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """El bot deposita (o actualiza) un ticket. IDEMPOTENTE por `origen_externo`."""
    quien = _quien(ctx)
    t = (db.query(Ticket).filter(Ticket.origen_externo == payload.origen_externo)
         .with_for_update().one_or_none())
    if t is not None:
        _aplicar(t, payload, quien)
        db.flush()
        db.refresh(t)
        response.status_code = status.HTTP_200_OK
        return t
    t = Ticket(tenant_id=ctx.tenant_id, numero=payload.numero, canal=payload.canal,
               origen_externo=payload.origen_externo, estado="ABIERTO", created_by=ctx.user_id,
               eventos=[])
    _evento(t, payload.remitente or quien, f"Abierto desde {payload.grupo or payload.canal}")
    _aplicar(t, payload, quien)
    try:
        with db.begin_nested():
            db.add(t)
            db.flush()
    except IntegrityError:
        # Otro request ganó con el mismo origen (reintento en paralelo) → ese es.
        # Si lo que choca es el NÚMERO con otro origen, es un error del bot y
        # se dice: dos casos distintos no pueden compartir número.
        ganador = db.query(Ticket).filter(Ticket.origen_externo == payload.origen_externo).one_or_none()
        if ganador is None:
            raise HTTPException(status_code=409, detail=f"El ticket {payload.numero} ya existe con otro origen")
        response.status_code = status.HTTP_200_OK
        return ganador
    db.refresh(t)
    return t


@router.get("", response_model=Page[TicketOut])
def listar(
    estado: Optional[str] = Query(default=None, max_length=40),
    q: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """`estado` acepta una lista separada por comas («ABIERTO,EN_CURSO»)."""
    query = db.query(Ticket)
    if estado:
        query = query.filter(Ticket.estado.in_([e.strip().upper() for e in estado.split(",") if e.strip()]))
    if q:
        for termino in q.split():
            like = f"%{termino}%"
            filtros = (Ticket.archivo_nombre.ilike(like) | Ticket.nota.ilike(like)
                       | Ticket.grupo.ilike(like) | Ticket.que_paso.ilike(like)
                       | Ticket.resolucion.ilike(like))
            if termino.lstrip("#").isdigit():
                filtros = filtros | (Ticket.numero == int(termino.lstrip("#")))
            query = query.filter(filtros)
    return paginate(query.order_by(Ticket.numero.desc()), TicketOut, limit, offset)


@router.get("/resumen", response_model=TicketResumenOut)
def resumen(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Lo que el menú lateral enseña como contador."""
    n = db.query(func.count(Ticket.id)).filter(Ticket.estado.in_(("ABIERTO", "EN_CURSO"))).scalar()
    return TicketResumenOut(abiertos=int(n or 0))


@router.get("/acciones/pendientes", response_model=list[TicketAccionPendienteOut])
def acciones_pendientes(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """El bot reclama lo que se pidió desde la pantalla. Reclamar marca
    `accion_tomada_at` (skip_locked): dos pasadas del bot no ejecutan dos veces
    el mismo reproceso. Si falla, el /ack con ok=false lo regresa a ABIERTO."""
    filas = (db.query(Ticket)
             .filter(Ticket.accion_pedida.isnot(None), Ticket.accion_tomada_at.is_(None))
             .order_by(Ticket.accion_pedida_at.asc())
             .with_for_update(skip_locked=True).limit(20).all())
    out = []
    for t in filas:
        t.accion_tomada_at = _ahora()
        ultimo = next((e for e in reversed(t.eventos or []) if e.get("nota")), None)
        out.append(TicketAccionPendienteOut(
            id=t.id, numero=t.numero, origen_externo=t.origen_externo,
            archivo_nombre=t.archivo_nombre, accion=t.accion_pedida,
            pedida_por=t.accion_pedida_por, nota=(ultimo or {}).get("nota")))
    db.flush()
    return out


@router.get("/{ticket_id}", response_model=TicketDetailOut)
def detalle(
    ticket_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, Ticket, ticket_id, soft=False)


@router.get("/{ticket_id}/foto")
def foto(
    ticket_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    t = get_or_404(db, Ticket, ticket_id, soft=False)
    if not t.foto:
        raise HTTPException(status_code=404, detail="Este ticket no trae foto")
    return Response(content=t.foto, media_type=t.foto_mime or "image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.post("/{ticket_id}/accion", response_model=TicketDetailOut)
def pedir_accion(
    ticket_id: UUID,
    payload: TicketAccionIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Pide al bot que resuelva el caso.

    CERRAR se cierra AQUÍ mismo (no hay nada que ejecutar) y además se le avisa
    al bot para que deje de perseguir la foto. EXTRA queda EN_CURSO hasta que el
    bot la registre y mande el RESUELTO con la OC.
    """
    t = get_or_404(db, Ticket, ticket_id, soft=False, for_update=True)
    if t.estado in _TERMINALES:
        raise HTTPException(status_code=409, detail=f"El ticket {t.numero} ya está {t.estado.lower()}")
    if t.accion_pedida and t.accion_tomada_at is None:
        raise HTTPException(status_code=409, detail="Ya hay una acción pedida esperando al bot")
    if payload.accion == "EXTRA" and "EXTRA" not in (t.acciones or []):
        raise HTTPException(status_code=422, detail="Este caso no se resuelve sumándolo como complemento")
    quien = _quien(ctx)
    t.accion_pedida = payload.accion
    t.accion_pedida_por = quien
    t.accion_pedida_at = _ahora()
    t.accion_tomada_at = None
    ev = {"ts": _ahora().isoformat(), "quien": quien,
          "texto": ("Pidió sumarlo como complemento (EXTRA)" if payload.accion == "EXTRA"
                    else "Lo cerró: ya quedó por otro lado")}
    if payload.nota:
        ev["nota"] = payload.nota.strip()
        ev["texto"] += f" — {payload.nota.strip()}"
    t.eventos = [*(t.eventos or []), ev]
    flag_modified(t, "eventos")
    if payload.accion == "CERRAR":
        t.estado = "CERRADO"
        t.resuelto_at = _ahora()
        t.resuelto_por = quien
        t.resolucion = payload.nota.strip() if payload.nota else "Cerrado a mano desde el Facturador"
    else:
        t.estado = "EN_CURSO"
    db.flush()
    db.refresh(t)
    return t


@router.post("/{ticket_id}/ack", response_model=TicketDetailOut)
def ack(
    ticket_id: UUID,
    payload: TicketAckIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    t = get_or_404(db, Ticket, ticket_id, soft=False, for_update=True)
    if not payload.ok and t.estado == "EN_CURSO":
        t.estado = "ABIERTO"
        t.accion_pedida = None
    t.accion_tomada_at = t.accion_tomada_at or _ahora()
    _evento(t, "WhatsApp", (payload.detalle or ("El bot tomó la acción" if payload.ok
                                                 else "El bot no pudo aplicar la acción")))
    db.flush()
    db.refresh(t)
    return t


@router.post("/{ticket_id}/comentarios", response_model=TicketDetailOut)
def comentar(
    ticket_id: UUID,
    payload: TicketComentarioIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    t = get_or_404(db, Ticket, ticket_id, soft=False, for_update=True)
    _evento(t, _quien(ctx), payload.texto.strip())
    db.flush()
    db.refresh(t)
    return t
