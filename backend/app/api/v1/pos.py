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
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.attributes import flag_modified

from ...core.rbac import AuthContext, get_auth_context, get_tenant_db, require_permission
from ...models import Remision, Tenant
from ...schemas.common import Page
from ...schemas.remision import RemisionOut
from ...services.pos import (
    COMPLETADO,
    ETAPAS_ORDEN,
    PERMISO_ACCION,
    PERMISO_MENU,
    etapa_salida_inventario,
    etapas_activas,
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


class PosConfigIn(BaseModel):
    activo: bool = False
    etapas: list[str] = Field(default_factory=lambda: list(ETAPAS_ORDEN), max_length=4)
    credito: bool = False
    inventario_sale_en: str = Field(default="surtido", max_length=10)
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
    cfg["etapas_visibles"] = [
        e for e in etapas_activas(cfg) if _tiene(ctx, PERMISO_MENU[e])
    ]
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
def _estampar(rem: Remision, etapa: str, ctx: AuthContext) -> None:
    rem.pos_asignaciones = {
        **(rem.pos_asignaciones or {}),
        etapa: {"user_id": str(ctx.user_id), "at": datetime.now(timezone.utc).isoformat()},
    }


def _salida_inventario(db: Session, ctx: AuthContext, rem: Remision, cfg: dict) -> None:
    """Salida directa (mismo motor de remisiones); idempotente: solo BORRADOR."""
    if rem.estado == "BORRADOR":
        reservar_stock_remision(
            db, ctx, rem, permitir_negativos=bool(cfg.get("permitir_sobregiro")),
        )


@router.post("/remisiones/{rem_id}/iniciar", response_model=RemisionOut)
def iniciar_en_pos(
    rem_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Mete una remisión BORRADOR al pipeline del POS (cae en la primera cola).
    La captura nativa del POS (Fase 1) crea la remisión y llama esto mismo."""
    _exigir(ctx, PERMISO_ACCION["pedido"])
    cfg = pos_config(_cargar_tenant(db, ctx.tenant_id))
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
    if destino == COMPLETADO:
        # Mostrador exprés (flujo de solo captura): la salida ocurre al crear.
        _salida_inventario(db, ctx, rem, cfg)
    rem.updated_by = ctx.user_id
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
    if etapa not in ETAPAS_ORDEN:
        raise HTTPException(status_code=422, detail=f"Etapa desconocida: {etapa}")
    _exigir(ctx, PERMISO_MENU[etapa])
    cfg = pos_config(_cargar_tenant(db, ctx.tenant_id))
    if etapa not in etapas_activas(cfg):
        raise HTTPException(status_code=422, detail=f"La etapa {etapa} no está activa en el flujo")
    query = (
        db.query(Remision)
        .options(joinedload(Remision.factura))
        .filter(Remision.deleted_at.is_(None), Remision.pos_etapa == etapa)
        .order_by(Remision.created_at.asc())
    )
    return paginate(query, RemisionOut, min(limit, 200), max(offset, 0))


class AvanzarIn(BaseModel):
    # Protección contra doble clic / carreras: la etapa que el cliente CREE que
    # completa debe ser la etapa real del pedido.
    etapa: str = Field(max_length=12)


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
    if payload.etapa not in ETAPAS_ORDEN:
        raise HTTPException(status_code=422, detail=f"Etapa desconocida: {payload.etapa}")
    _exigir(ctx, PERMISO_ACCION[payload.etapa])
    cfg = pos_config(_cargar_tenant(db, ctx.tenant_id))
    rem = get_or_404(db, Remision, rem_id, for_update=True)
    if rem.pos_etapa != payload.etapa:
        raise HTTPException(
            status_code=409,
            detail=f"El pedido está en la etapa {rem.pos_etapa or 'ninguna'}, no en {payload.etapa}",
        )

    _estampar(rem, payload.etapa, ctx)
    if etapa_salida_inventario(cfg) == payload.etapa:
        _salida_inventario(db, ctx, rem, cfg)
    rem.pos_etapa = siguiente_etapa(cfg, payload.etapa)
    rem.updated_by = ctx.user_id
    db.flush()
    db.refresh(rem)
    return rem
