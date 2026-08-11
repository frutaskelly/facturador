"""POS — Fase 0: config del flujo + motor de transiciones + colas.

El pedido del POS ES una remisión (misma pieza que ya maneja inventario,
impuestos, folios, devoluciones y facturación en lote). Este router solo agrega
la capa de pipeline: en qué estación espera cada pedido y quién completó qué.

Permisos (seed 0003): ver cola de una etapa → `menu:pos.<etapa>`; completar una
etapa → `pedido:capturar|cobrar|surtir|entregar`. OWNER bypassa. La config se
edita con `membership:gestionar` (misma perm admin que Empresa/Correo).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.attributes import flag_modified

from ...core.rbac import AuthContext, get_auth_context, get_tenant_db, require_permission
from ...models import Cliente, Pago, PosCorte, Remision, Tenant
from ...schemas.common import Page
from ...schemas.remision import RemisionOut
from ...services import credito as credito_svc
from ...services import pos_pulse
from ...services.pos import (
    COMPLETADO,
    SALE_AL_CREAR,
    etapa_salida_inventario,
    etapas_flujo,
    etiqueta,
    permiso_accion,
    permiso_menu,
    pos_config,
    primera_cola,
    siguiente_etapa,
    validar_config,
)
from ._helpers import get_or_404, paginate
from .remisiones import reservar_stock_remision

router = APIRouter(prefix="/pos", tags=["pos"])

_ADMIN = "membership:gestionar"


def _tiene(ctx: AuthContext, permiso: str) -> bool:
    return ctx.is_owner or permiso in ctx.permissions


def _exigir(ctx: AuthContext, permiso: str) -> None:
    if not _tiene(ctx, permiso):
        raise HTTPException(status_code=403, detail=f"Requiere el permiso {permiso}")


def _cargar_tenant(db: Session, tenant_id) -> Tenant:
    return db.query(Tenant).filter(Tenant.id == tenant_id).one()


# ─── Config del flujo (Ajustes › Punto de venta) ─────────────────────────────
class PosTicketIn(BaseModel):
    formato: str = Field(default="80mm", max_length=10)
    auto_imprimir: bool = False


class PosEtapaCustomIn(BaseModel):
    id: str = Field(max_length=30)
    nombre: str = Field(max_length=40)
    # Quién la trabaja (permiso de acción del seed 0003).
    permiso: str = Field(default="pedido:surtir", max_length=30)


class PosConfigIn(BaseModel):
    activo: bool = False
    etapas: list[str] = Field(default_factory=lambda: ["pedido", "caja", "almacen", "salida"], max_length=8)
    etapas_custom: list[PosEtapaCustomIn] = Field(default_factory=list, max_length=6)
    credito: bool = False
    inventario_sale_en: str = Field(default="almacen", max_length=30)
    serie_id: Optional[UUID] = None
    permitir_sobregiro: bool = False
    ticket: PosTicketIn = PosTicketIn()


@router.get("/config")
def get_config(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Config vigente + qué etapas puede VER este usuario (para armar el nav)."""
    cfg = pos_config(_cargar_tenant(db, ctx.tenant_id))
    flujo = etapas_flujo(cfg)
    cfg["etapas_visibles"] = [e for e in flujo if _tiene(ctx, permiso_menu(cfg, e))]
    cfg["etiquetas"] = {e: etiqueta(cfg, e) for e in flujo}
    cfg["puede_configurar"] = _tiene(ctx, _ADMIN)
    return cfg


@router.put("/config")
def put_config(
    payload: PosConfigIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_ADMIN)),
):
    nuevo = payload.model_dump(mode="json")
    error = validar_config(nuevo)
    if error:
        raise HTTPException(status_code=422, detail=error)
    tenant = _cargar_tenant(db, ctx.tenant_id)
    tenant.config = {**(tenant.config or {}), "pos": nuevo}
    flag_modified(tenant, "config")
    db.flush()
    return pos_config(tenant)


# ─── Motor: iniciar / cola / avanzar ─────────────────────────────────────────
def _estampar(rem: Remision, etapa: str, ctx: AuthContext, *, nota: Optional[str] = None) -> None:
    marca = {"user_id": str(ctx.user_id), "at": datetime.now(timezone.utc).isoformat()}
    if nota:
        marca["nota"] = nota[:200]
    rem.pos_asignaciones = {**(rem.pos_asignaciones or {}), etapa: marca}


def _salida_inventario(
    db: Session, ctx: AuthContext, rem: Remision, cfg: dict, pesos: dict | None = None
) -> None:
    """Salida directa (mismo motor de remisiones); idempotente: solo BORRADOR.
    `pesos` (linea_id → cantidad_base real) permite el peso real del surtido."""
    if rem.estado == "BORRADOR":
        reservar_stock_remision(
            db, ctx, rem, permitir_negativos=bool(cfg.get("permitir_sobregiro")), pesos=pesos,
        )


@router.post("/remisiones/{rem_id}/iniciar", response_model=RemisionOut)
def iniciar_en_pos(
    rem_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Mete una remisión BORRADOR al pipeline del POS (cae en la primera cola).
    La captura nativa del POS (Fase 1) crea la remisión y llama esto mismo."""
    cfg = pos_config(_cargar_tenant(db, ctx.tenant_id))
    _exigir(ctx, permiso_accion(cfg, "pedido"))
    if not cfg.get("activo"):
        raise HTTPException(status_code=409, detail="El POS está desactivado (Ajustes › Punto de venta)")
    rem = get_or_404(db, Remision, rem_id, for_update=True)
    if rem.pos_etapa is not None:
        raise HTTPException(status_code=409, detail=f"La remisión ya está en el POS (etapa {rem.pos_etapa})")
    if rem.estado != "BORRADOR":
        raise HTTPException(status_code=409, detail=f"Solo entra al POS una remisión en BORRADOR (actual: {rem.estado})")

    _estampar(rem, "pedido", ctx)
    destino = primera_cola(cfg)
    rem.pos_etapa = destino
    # Sale al crear si así se configuró, o si el flujo es de pura captura.
    if destino == COMPLETADO or etapa_salida_inventario(cfg) == SALE_AL_CREAR:
        _salida_inventario(db, ctx, rem, cfg)
    rem.updated_by = ctx.user_id
    pos_pulse.bump(ctx.tenant_id)
    db.flush()
    db.refresh(rem)
    return rem


@router.get("/cola/{etapa}", response_model=Page[RemisionOut])
def cola(
    etapa: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Pedidos esperando en una estación (los más viejos primero)."""
    cfg = pos_config(_cargar_tenant(db, ctx.tenant_id))
    if etapa not in etapas_flujo(cfg):
        raise HTTPException(status_code=422, detail=f"La etapa {etapa} no está en el flujo")
    _exigir(ctx, permiso_menu(cfg, etapa))
    query = (
        db.query(Remision)
        .options(joinedload(Remision.factura))
        .filter(Remision.deleted_at.is_(None), Remision.pos_etapa == etapa)
        .order_by(Remision.created_at.asc())
    )
    return paginate(query, RemisionOut, min(limit, 200), max(offset, 0))


class PesoLineaIn(BaseModel):
    linea_id: UUID
    cantidad_base: Decimal = Field(gt=0)   # peso/medida real en unidad base


class AvanzarIn(BaseModel):
    # Protección contra doble clic / carreras: la etapa que el cliente CREE que
    # completa debe ser la etapa real del pedido.
    etapa: str = Field(max_length=30)
    # Almacén: peso real del surtido (catch-weight) si la salida ocurre aquí.
    pesos: Optional[list[PesoLineaIn]] = None
    # Salida: quién recibe / referencia de entrega (queda en la asignación).
    nota: Optional[str] = Field(default=None, max_length=200)


@router.post("/remisiones/{rem_id}/avanzar", response_model=RemisionOut)
def avanzar(
    rem_id: UUID,
    payload: AvanzarIn = Body(...),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Completa la etapa actual del pedido y lo pasa a la siguiente del flujo
    configurado. Side-effect: al completar la etapa mapeada de
    `inventario_sale_en`, sale el inventario (salida directa).

    Nota Fase 2: cuando la etapa es `caja`, este endpoint será reemplazado por
    /cobrar (exigirá el pago); por ahora avanza sin registrar pago.
    """
    cfg = pos_config(_cargar_tenant(db, ctx.tenant_id))
    if payload.etapa not in etapas_flujo(cfg):
        raise HTTPException(status_code=422, detail=f"La etapa {payload.etapa} no está en el flujo")
    _exigir(ctx, permiso_accion(cfg, payload.etapa))
    rem = get_or_404(db, Remision, rem_id, for_update=True)
    if rem.pos_etapa != payload.etapa:
        raise HTTPException(
            status_code=409,
            detail=f"El pedido está en la etapa {rem.pos_etapa or 'ninguna'}, no en {payload.etapa}",
        )

    pesos = {p.linea_id: p.cantidad_base for p in (payload.pesos or [])}
    _completar_etapa(db, ctx, rem, cfg, payload.etapa, pesos=pesos or None, nota=payload.nota)
    db.flush()
    db.refresh(rem)
    return rem


def _completar_etapa(
    db: Session, ctx: AuthContext, rem: Remision, cfg: dict, etapa: str,
    *, pesos: dict | None = None, nota: Optional[str] = None,
) -> None:
    """Marca la etapa completada: estampa quién/cuándo (con nota opcional),
    dispara la salida de inventario si toca, y avanza a la siguiente etapa."""
    _estampar(rem, etapa, ctx, nota=nota)
    if etapa_salida_inventario(cfg) == etapa:
        _salida_inventario(db, ctx, rem, cfg, pesos=pesos)
    rem.pos_etapa = siguiente_etapa(cfg, etapa)
    rem.updated_by = ctx.user_id
    pos_pulse.bump(ctx.tenant_id)   # realtime: avisa a las estaciones


# ─── Caja: cobro (efectivo / tarjeta / crédito) ──────────────────────────────
_FORMA_SAT = {"efectivo": "01", "tarjeta": "04", "credito": "99"}


class PagoIn(BaseModel):
    forma: str = Field(max_length=12)          # efectivo | tarjeta | credito
    monto: Decimal = Field(gt=0)
    referencia: Optional[str] = Field(default=None, max_length=100)


class CobrarIn(BaseModel):
    pagos: list[PagoIn] = Field(min_length=1, max_length=6)


@router.post("/remisiones/{rem_id}/cobrar", response_model=RemisionOut)
def cobrar(
    rem_id: UUID,
    payload: CobrarIn = Body(...),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Cobra un pedido en la etapa de caja: registra los pagos (efectivo/tarjeta/
    crédito), valida que sumen el total, aplica el crédito al saldo del cliente
    y completa la etapa. El efectivo entra al corte abierto del cajero (si hay).
    """
    cfg = pos_config(_cargar_tenant(db, ctx.tenant_id))
    _exigir(ctx, permiso_accion(cfg, "caja"))
    if "caja" not in etapas_flujo(cfg):
        raise HTTPException(status_code=422, detail="El flujo del POS no tiene etapa de caja")
    rem = get_or_404(db, Remision, rem_id, for_update=True)
    if rem.pos_etapa != "caja":
        raise HTTPException(
            status_code=409,
            detail=f"El pedido no está en caja (etapa {rem.pos_etapa or 'ninguna'})",
        )

    formas = {p.forma for p in payload.pagos}
    invalidas = formas - set(_FORMA_SAT)
    if invalidas:
        raise HTTPException(status_code=422, detail=f"Forma de pago no soportada: {', '.join(invalidas)}")
    total = Decimal(rem.total or 0)
    pagado = sum((Decimal(p.monto) for p in payload.pagos), Decimal("0"))
    if not cfg.get("credito") and "credito" in formas:
        raise HTTPException(status_code=422, detail="La venta a crédito está desactivada (Ajustes › Punto de venta)")
    # El pago debe cuadrar con el total (el efectivo puede exceder → cambio, se
    # informa; el resto no debe sobrar).
    credito_monto = sum((Decimal(p.monto) for p in payload.pagos if p.forma == "credito"), Decimal("0"))
    no_efectivo = sum((Decimal(p.monto) for p in payload.pagos if p.forma != "efectivo"), Decimal("0"))
    if no_efectivo > total:
        raise HTTPException(status_code=422, detail="Tarjeta/crédito no pueden exceder el total")
    if pagado < total:
        raise HTTPException(status_code=422, detail=f"Falta cobrar ${total - pagado:,.2f}")

    cliente = db.query(Cliente).filter(Cliente.id == rem.cliente_facturacion_id).one()
    if credito_monto > 0:
        credito_svc.validar_credito_disponible(cliente, credito_monto)
        credito_svc.aplicar_cargo_credito(cliente, credito_monto)
    credito_svc.registrar_venta(cliente, total)

    corte = _corte_abierto(db, ctx)
    for p in payload.pagos:
        db.add(Pago(
            tenant_id=ctx.tenant_id, cliente_id=cliente.id, remision_id=rem.id,
            corte_id=corte.id if (corte is not None and p.forma == "efectivo") else None,
            monto=Decimal(p.monto), forma_pago=_FORMA_SAT[p.forma],
            referencia=p.referencia, created_by=ctx.user_id,
        ))

    _completar_etapa(db, ctx, rem, cfg, "caja")
    db.flush()
    db.refresh(rem)
    return rem


# ─── Corte de caja (turno con fondo inicial + arqueo) ────────────────────────
def _corte_abierto(db: Session, ctx: AuthContext) -> Optional[PosCorte]:
    return (
        db.query(PosCorte)
        .filter(PosCorte.user_id == ctx.user_id, PosCorte.estado == "ABIERTO")
        .one_or_none()
    )


def _resumen_corte(db: Session, corte: PosCorte) -> dict:
    """Totales por forma de pago del turno + efectivo esperado y descuadre."""
    filas = (
        db.query(Pago.forma_pago, func.coalesce(func.sum(Pago.monto), 0))
        .filter(Pago.corte_id == corte.id)
        .group_by(Pago.forma_pago)
        .all()
    )
    por_forma = {f: Decimal(m) for f, m in filas}
    # Solo el efectivo se enlaza al corte; tarjeta/crédito no tocan la caja física.
    efectivo = por_forma.get("01", Decimal("0"))
    esperado = Decimal(corte.fondo_inicial or 0) + efectivo
    contado = Decimal(corte.efectivo_contado) if corte.efectivo_contado is not None else None
    return {
        "id": str(corte.id),
        "estado": corte.estado,
        "fondo_inicial": corte.fondo_inicial,
        "efectivo_ventas": efectivo,
        "efectivo_esperado": esperado,
        "efectivo_contado": contado,
        "descuadre": (contado - esperado) if contado is not None else None,
        "abierto_at": corte.abierto_at,
        "cerrado_at": corte.cerrado_at,
    }


class AbrirCorteIn(BaseModel):
    fondo_inicial: Decimal = Field(default=Decimal("0"), ge=0)


@router.post("/corte/abrir")
def abrir_corte(
    payload: AbrirCorteIn = Body(default=AbrirCorteIn()),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Abre el turno de caja del cajero con su fondo inicial."""
    _exigir(ctx, "pedido:cobrar")
    if _corte_abierto(db, ctx) is not None:
        raise HTTPException(status_code=409, detail="Ya tienes un corte abierto; ciérralo primero")
    corte = PosCorte(tenant_id=ctx.tenant_id, user_id=ctx.user_id,
                     fondo_inicial=Decimal(payload.fondo_inicial))
    db.add(corte)
    db.flush()
    return _resumen_corte(db, corte)


@router.get("/corte/actual")
def corte_actual(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Corte abierto del cajero con su resumen en vivo (null si no hay)."""
    _exigir(ctx, "pedido:cobrar")
    corte = _corte_abierto(db, ctx)
    return _resumen_corte(db, corte) if corte is not None else None


class CerrarCorteIn(BaseModel):
    efectivo_contado: Decimal = Field(ge=0)
    notas: Optional[str] = Field(default=None, max_length=500)


@router.post("/corte/cerrar")
def cerrar_corte(
    payload: CerrarCorteIn = Body(...),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Cierra el turno declarando el efectivo contado; devuelve el arqueo con
    el descuadre (contado − esperado)."""
    _exigir(ctx, "pedido:cobrar")
    corte = _corte_abierto(db, ctx)
    if corte is None:
        raise HTTPException(status_code=409, detail="No tienes un corte abierto")
    corte.efectivo_contado = Decimal(payload.efectivo_contado)
    corte.notas = (payload.notas or "").strip() or None
    corte.estado = "CERRADO"
    corte.cerrado_at = func.now()
    db.flush()
    db.refresh(corte)
    return _resumen_corte(db, corte)


# ─── Realtime: pulso + tablero de Operaciones ────────────────────────────────
@router.get("/pulse")
def pulse(
    ctx: AuthContext = Depends(get_auth_context),
):
    """Contador de cambios del POS del tenant. Las estaciones lo consultan cada
    pocos segundos y recargan su cola solo cuando cambia (casi-tiempo-real sin
    WebSocket). Sin Redis devuelve 0 (las estaciones caen a recarga periódica)."""
    return {"v": pos_pulse.read(ctx.tenant_id)}


@router.get("/operaciones")
def operaciones(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Tablero de supervisión: conteo por etapa del flujo, ventas y cobros del
    día por forma de pago, y los pedidos activos con su progreso. Requiere ver
    alguna estación del POS."""
    cfg = pos_config(_cargar_tenant(db, ctx.tenant_id))
    flujo = etapas_flujo(cfg)
    if not any(_tiene(ctx, permiso_menu(cfg, e)) for e in flujo):
        raise HTTPException(status_code=403, detail="Sin acceso a estaciones del POS")

    # Conteo de pedidos esperando en cada etapa activa.
    filas = (
        db.query(Remision.pos_etapa, func.count(Remision.id))
        .filter(Remision.deleted_at.is_(None), Remision.pos_etapa.isnot(None))
        .group_by(Remision.pos_etapa)
        .all()
    )
    por_etapa = {e: 0 for e in flujo}
    completados_activos = 0
    for etapa, n in filas:
        if etapa == COMPLETADO:
            completados_activos = int(n)
        elif etapa in por_etapa:
            por_etapa[etapa] = int(n)

    # Cobros de HOY por forma de pago (SAT → etiqueta).
    _ETIQUETA_FORMA = {"01": "efectivo", "04": "tarjeta", "99": "credito"}
    cobros = (
        db.query(Pago.forma_pago, func.coalesce(func.sum(Pago.monto), 0))
        .filter(Pago.fecha == func.current_date())
        .group_by(Pago.forma_pago)
        .all()
    )
    cobrado = {"efectivo": 0.0, "tarjeta": 0.0, "credito": 0.0}
    cobrado_total = 0.0
    for forma, monto in cobros:
        m = float(monto)
        cobrado_total += m
        etiqueta_forma = _ETIQUETA_FORMA.get(forma)
        if etiqueta_forma:
            cobrado[etiqueta_forma] += m

    # Ventas del día (remisiones que iniciaron hoy en el POS).
    ventas_hoy = (
        db.query(func.coalesce(func.sum(Remision.total), 0), func.count(Remision.id))
        .filter(
            Remision.deleted_at.is_(None),
            Remision.pos_etapa.isnot(None),
            func.date(Remision.created_at) == func.current_date(),
        )
        .one()
    )

    # Pedidos activos (en alguna cola) con su progreso.
    activos = (
        db.query(Remision)
        .options(joinedload(Remision.factura))
        .filter(
            Remision.deleted_at.is_(None),
            Remision.pos_etapa.isnot(None),
            Remision.pos_etapa != COMPLETADO,
        )
        .order_by(Remision.created_at.asc())
        .limit(100)
        .all()
    )
    activos_out = [
        {
            "id": str(r.id),
            "folio_interno": r.folio_interno,
            "cliente_id": str(r.cliente_facturacion_id),
            "total": r.total,
            "pos_etapa": r.pos_etapa,
            "pos_asignaciones": r.pos_asignaciones or {},
            "created_at": r.created_at,
        }
        for r in activos
    ]

    return {
        "flujo": flujo,
        "etiquetas": {e: etiqueta(cfg, e) for e in flujo},
        "por_etapa": por_etapa,
        "completados_activos": completados_activos,
        "cobrado_hoy": cobrado,
        "cobrado_hoy_total": cobrado_total,
        "ventas_hoy_total": float(ventas_hoy[0]),
        "pedidos_hoy": int(ventas_hoy[1]),
        "activos": activos_out,
    }
