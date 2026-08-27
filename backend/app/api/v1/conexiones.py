"""Conexiones — enchufar un sistema externo sin repartir contraseñas.

La pantalla que sirve este router responde dos cosas y nada más: cómo conectar
Smart Supply, y si está entrando lo que debe. Por eso el estado no devuelve
configuración sino ACTIVIDAD (última orden, cuántas hoy, cuántas sin resolver):
una vez conectado, eso es lo único que alguien va a venir a mirar.

Gestionar conexiones es cosa del dueño o de quien administre la empresa, así que
se reusa `membership:gestionar` — el mismo permiso que abre Empresa y Correo.
Leerlas pide `menu:ajustes.usuarios` no: basta con poder gestionar.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.rbac import PERMISOS_CONEXION, AuthContext, get_tenant_db, require_permission
from ...models import Conexion, OCRecibida, Tenant
from ...models.conexion import generar_clave, hash_clave, pista_de
from ...schemas.conexion import (
    ActividadConexionOut,
    ClaveNuevaOut,
    ConexionEstadoOut,
    ConexionOut,
    PruebaOut,
)
from ._helpers import get_or_404

router = APIRouter(prefix="/conexiones", tags=["conexiones"])

_GESTIONAR = "membership:gestionar"

# Por ahora solo Smart Supply. El catálogo vive aquí para que la pantalla pueda
# listar lo que se puede conectar sin conocer nada más.
CATALOGO = {
    "SMART_SUPPLY": {
        "nombre": "Smart Supply",
        "descripcion": "Órdenes de compra por WhatsApp",
    },
}

# Sin vencimiento (decisión del dueño): una clave que caduca sola se cae de
# madrugada. En vez de eso, al año la pantalla sugiere rotarla.
_DIAS_PARA_SUGERIR_ROTAR = 365


def _viva(db: Session, tipo: str) -> Optional[Conexion]:
    return (
        db.query(Conexion)
        .filter(Conexion.tipo == tipo, Conexion.estado != "REVOCADA")
        .one_or_none()
    )


def _estado(db: Session, tipo: str) -> ConexionEstadoOut:
    meta = CATALOGO[tipo]
    con = _viva(db, tipo)
    out = ConexionEstadoOut(tipo=tipo, nombre=meta["nombre"])
    if con is None:
        return out

    out.conexion = ConexionOut.model_validate(con)
    creada = con.created_at
    if creada is not None:
        if creada.tzinfo is None:
            creada = creada.replace(tzinfo=timezone.utc)
        dias = (datetime.now(timezone.utc) - creada).days
        out.dias_desde_creacion = dias
        out.conviene_rotar = dias >= _DIAS_PARA_SUGERIR_ROTAR

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


@router.post("/{tipo}/clave", response_model=ClaveNuevaOut, status_code=status.HTTP_201_CREATED)
def generar(
    tipo: str,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_GESTIONAR)),
):
    """Genera la clave. Se devuelve en claro UNA vez y ya no se puede volver a leer.

    Si ya había una viva se revoca en el acto: «generar otra» y «la anterior deja
    de servir» tienen que ser el mismo gesto, o quedarían dos claves buenas y
    nadie sabría cuál está usando el bot.
    """
    tipo = tipo.upper()
    if tipo not in CATALOGO:
        raise HTTPException(status_code=404, detail="Ese sistema no se puede conectar")

    anterior = _viva(db, tipo)
    if anterior is not None:
        anterior.estado = "REVOCADA"
        anterior.revocada_at = datetime.now(timezone.utc)
        db.flush()

    clave = generar_clave()
    con = Conexion(
        tenant_id=ctx.tenant_id,
        tipo=tipo,
        nombre=CATALOGO[tipo]["nombre"],
        clave_hash=hash_clave(clave),
        clave_pista=pista_de(clave),
        estado="PENDIENTE",
        created_by=ctx.user_id,
    )
    db.add(con)
    db.flush()
    db.refresh(con)
    return ClaveNuevaOut(
        clave=clave,
        conexion=ConexionOut.model_validate(con),
        instruccion_whatsapp=f"smart supply: conectar facturador {clave}",
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
    con.estado = "REVOCADA"
    con.revocada_at = datetime.now(timezone.utc)
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
    ctx: AuthContext = Depends(require_permission("menu:remisiones")),
):
    """Confirma que una clave sirve, sin escribir nada.

    Lo llama el bot al conectarse (para poder responder «listo» en el chat) y la
    pantalla con el botón «Probar conexión». Pide un permiso que TODA conexión
    tiene, así que sirve para ambas identidades.
    """
    t = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one_or_none()
    if ctx.conexion_id is not None:
        return PruebaOut(
            ok=True,
            mensaje="Clave válida. Las órdenes que mandes aparecerán en la bandeja.",
            tenant=t.legal_name if t else None,
            permisos=sorted(PERMISOS_CONEXION),
        )
    return PruebaOut(
        ok=True,
        mensaje="Sesión válida (no es una clave de conexión).",
        tenant=t.legal_name if t else None,
        permisos=sorted(ctx.permissions),
    )
