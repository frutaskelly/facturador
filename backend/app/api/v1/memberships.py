"""Memberships — list members of the tenant and manage their role/status.

Reads gated by `menu:ajustes.usuarios`; writes by `membership:gestionar`.

RLS scopes every query to the current tenant. You can reassign a member to any
preset role or one of your own custom roles, activate/deactivate them, or remove
them. You cannot touch your own membership (prevents self-lockout). Inviting
brand-new users is an operator/provisioning flow, not exposed here.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from ...core.db import get_db
from ...core.rbac import AuthContext, get_tenant_db, invalidate_auth_cache, require_permission
from ...models import Membership, Role, Tenant, User
from ...schemas.membership import (
    CambiarPasswordIn,
    CrearUsuarioIn,
    MembershipOut,
    MembershipUpdate,
)
from ...services import supabase_admin
from ._helpers import ensure_fk, get_or_404

router = APIRouter(prefix="/memberships", tags=["membresías"])

_READ = "menu:ajustes.usuarios"
_WRITE = "membership:gestionar"


def _es_owner_preset(role: Role | None) -> bool:
    return role is not None and bool(role.es_preset) and role.nombre == "OWNER"


def _guard_owner(db: Session, ctx: AuthContext, m: Membership, accion: str) -> None:
    """Tocar la membresía de un OWNER (cambiarle rol, borrarla) es tomar el
    control de la empresa: solo otro OWNER puede."""
    rol = db.query(Role).filter(Role.id == m.role_id).one_or_none()
    if _es_owner_preset(rol) and not ctx.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Solo el dueño (OWNER) puede {accion} la membresía de un OWNER",
        )


def _es_owner_en_alguna_empresa(db: Session, user_id) -> bool:
    """¿El usuario es OWNER (preset) de ALGUNA empresa? La contraseña es global
    (cuenta Supabase), así que resetearla toma su cuenta en TODAS — incluida una
    empresa-hija del grupo donde sea dueño aunque aquí tenga un rol bajo."""
    return (
        db.query(Membership.id)
        .join(Role, Role.id == Membership.role_id)
        .filter(
            Membership.user_id == user_id,
            Role.es_preset.is_(True),
            Role.nombre == "OWNER",
        )
        .first()
    ) is not None


def _validar_scope(db: Session, tenant_id, ids) -> Optional[list]:
    """El candado por cliente solo acepta clientes VIVOS de ESTE tenant.
    Lista vacía o None = sin candado (se guarda NULL, no [])."""
    from ...models import Cliente

    if not ids:
        return None
    ids = list(dict.fromkeys(ids))
    vivos = {
        c for (c,) in db.query(Cliente.id).filter(
            Cliente.tenant_id == tenant_id,
            Cliente.id.in_(ids),
            Cliente.deleted_at.is_(None),
        )
    }
    malos = [str(i) for i in ids if i not in vivos]
    if malos:
        raise HTTPException(422, f"Clientes inexistentes en el candado: {', '.join(malos)}")
    return ids


def _m_out(m: Membership) -> MembershipOut:
    out = MembershipOut.model_validate(m)
    out.user_email = m.user.email if m.user else None
    out.user_full_name = m.user.full_name if m.user else None
    out.role_nombre = m.role.nombre if m.role else None
    return out


@router.get("", response_model=List[MembershipOut])
def list_memberships(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    rows = (
        db.query(Membership)
        .options(joinedload(Membership.user), joinedload(Membership.role))
        .order_by(Membership.created_at)
        .all()
    )
    return [_m_out(m) for m in rows]


@router.get("/{membership_id}", response_model=MembershipOut)
def get_membership(
    membership_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return _m_out(get_or_404(db, Membership, membership_id))


@router.patch("/{membership_id}", response_model=MembershipOut)
def update_membership(
    membership_id: UUID,
    payload: MembershipUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    m = get_or_404(db, Membership, membership_id)
    if m.user_id == ctx.user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No puedes modificar tu propia membresía",
        )
    # Anti-escalada: un admin con membership:gestionar no puede degradar/tocar
    # a un OWNER ni otorgar el rol OWNER — eso es transferir el control.
    _guard_owner(db, ctx, m, "modificar")
    data = payload.model_dump(exclude_unset=True)
    if "cliente_scope" in data:
        data["cliente_scope"] = _validar_scope(db, ctx.tenant_id, data["cliente_scope"])
    if "role_id" in data:
        # RLS: role must be a preset or one of this tenant's custom roles.
        ensure_fk(db, Role, data["role_id"], "role_id")
        nuevo_rol = db.query(Role).filter(Role.id == data["role_id"]).one_or_none()
        if _es_owner_preset(nuevo_rol) and not ctx.is_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo el dueño (OWNER) puede otorgar el rol OWNER",
            )
    for key, value in data.items():
        setattr(m, key, value)
    db.flush()
    db.refresh(m)
    invalidate_auth_cache()  # cambio de rol/estado → refresca permisos cacheados
    return _m_out(m)


@router.delete("/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_membership(
    membership_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("membership:eliminar")),
):
    m = get_or_404(db, Membership, membership_id)
    if m.user_id == ctx.user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No puedes eliminar tu propia membresía",
        )
    # Anti-takeover: quitar al OWNER de su propia empresa requiere ser OWNER.
    _guard_owner(db, ctx, m, "eliminar")
    db.delete(m)
    db.flush()
    invalidate_auth_cache()  # membresía eliminada → revoca permisos cacheados
    return None


@router.post("/usuarios", response_model=MembershipOut, status_code=status.HTTP_201_CREATED)
def crear_usuario(
    payload: CrearUsuarioIn,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    # rol debe existir (preset o de este tenant)
    role = db.query(Role).filter(Role.id == payload.role_id).one_or_none()
    if role is None or (role.tenant_id is not None and role.tenant_id != ctx.tenant_id):
        raise HTTPException(422, "Rol inválido")
    # Anti-escalada: OWNER bypassa TODOS los permisos; que un admin cree un
    # usuario OWNER equivale a autopromoción. Solo un OWNER otorga OWNER.
    if _es_owner_preset(role) and not ctx.is_owner:
        raise HTTPException(403, "Solo el dueño (OWNER) puede otorgar el rol OWNER")
    email = payload.email.strip().lower()
    user = db.query(User).filter(User.email == email).one_or_none()

    if user is not None:
        # Usuario ya existente (puede pertenecer a OTRA empresa). Validamos el
        # duplicado de membresía ANTES de crear nada y NO tocamos su contraseña:
        # resetearla afectaría a su cuenta global / a otra empresa. Para cambiar
        # contraseñas de miembros existentes está el endpoint /{id}/password.
        dup = (
            db.query(Membership)
            .filter(Membership.tenant_id == ctx.tenant_id, Membership.user_id == user.id)
            .one_or_none()
        )
        if dup is not None:
            raise HTTPException(409, "Ese usuario ya es miembro de esta empresa")
    else:
        # Usuario nuevo: creamos la cuenta de Auth (al final, para no dejar una
        # cuenta huérfana si algo falla antes del commit).
        if not supabase_admin.configured():
            raise HTTPException(503, "Supabase no está configurado para crear usuarios")
        try:
            auth_id = supabase_admin.create_auth_user(email, payload.password, payload.full_name)
        except supabase_admin.SupabaseAdminError as exc:
            raise HTTPException(502, f"No se pudo crear el usuario en Auth: {exc}")
        user = User(email=email, full_name=payload.full_name, auth_user_id=auth_id)
        db.add(user)
        db.flush()

    m = Membership(
        tenant_id=ctx.tenant_id, user_id=user.id, role_id=payload.role_id, active=True,
        cliente_scope=_validar_scope(db, ctx.tenant_id, payload.cliente_scope),
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    invalidate_auth_cache()  # nueva membresía → refresca permisos cacheados
    return _m_out(m)


@router.post("/{membership_id}/password", response_model=MembershipOut)
def cambiar_password(
    membership_id: UUID,
    payload: CambiarPasswordIn,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    m = (
        db.query(Membership)
        .filter(Membership.id == membership_id, Membership.tenant_id == ctx.tenant_id)
        .one_or_none()
    )
    if m is None:
        raise HTTPException(404, "Membership no encontrada")
    # Resetear la contraseña de un OWNER = tomar su cuenta. La credencial es
    # GLOBAL, así que basta que sea OWNER en CUALQUIER empresa (p. ej. una hija
    # del grupo donde aquí figure con un rol bajo): solo otro OWNER puede.
    if _es_owner_en_alguna_empresa(db, m.user_id) and not ctx.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el dueño (OWNER) puede cambiar la contraseña de un OWNER",
        )
    user = db.query(User).filter(User.id == m.user_id).one()
    if not user.auth_user_id:
        raise HTTPException(422, "El usuario no tiene cuenta de autenticación")
    # La contraseña es de la CUENTA GLOBAL del usuario (Supabase Auth), no de
    # esta empresa. Si también pertenece a empresas FUERA de tu grupo, resetearla
    # lo afectaría allá — se permite solo dentro del mismo grupo empresarial.
    # Solo cuentan membresías ACTIVAS en empresas VIVAS: una membresía inactiva o
    # de un tenant borrado no debe bloquear el reset para siempre.
    otras_ids = [
        t for (t,) in db.query(Membership.tenant_id)
        .filter(
            Membership.user_id == m.user_id,
            Membership.tenant_id != ctx.tenant_id,
            Membership.active.is_(True),
        )
        .all()
    ]
    if otras_ids:
        actual = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
        root_id = actual.parent_tenant_id or actual.id
        ajenas = (
            db.query(Tenant.id)
            .filter(
                Tenant.id.in_(otras_ids),
                Tenant.deleted_at.is_(None),
                Tenant.id != root_id,
                (Tenant.parent_tenant_id.is_(None)) | (Tenant.parent_tenant_id != root_id),
            )
            .count()
        )
        if ajenas:
            raise HTTPException(
                409,
                "El usuario también pertenece a empresas fuera de tu grupo; su contraseña es "
                "de su cuenta global. Pídele usar '¿Olvidaste tu contraseña?' en el login",
            )
    if not supabase_admin.configured():
        raise HTTPException(503, "Supabase no está configurado")
    try:
        supabase_admin.set_password(user.auth_user_id, payload.password)
    except supabase_admin.SupabaseAdminError as exc:
        raise HTTPException(502, f"No se pudo cambiar la contraseña: {exc}")
    return _m_out(m)


# ─── Empresas del grupo por usuario ──────────────────────────────────────────
# "En usuarios debemos asignar las empresas que pueden entrar" (dueño,
# 29-ago-2026). Una empresa = un tenant del grupo; el acceso = una membresía.
# Corre sobre get_db (sin GUC de RLS) con filtros explícitos, igual que
# crear_usuario/cambiar_password: son operaciones deliberadamente cross-tenant
# DENTRO del grupo, con el guard de administración por empresa destino.

def _grupo_ids(db: Session, tenant_id) -> list:
    """Los tenants VIVOS del grupo del tenant actual (raíz + hijas)."""
    actual = db.query(Tenant).filter(Tenant.id == tenant_id).one()
    root_id = actual.parent_tenant_id or actual.id
    return [
        t for (t,) in db.query(Tenant.id).filter(
            Tenant.deleted_at.is_(None),
            (Tenant.id == root_id) | (Tenant.parent_tenant_id == root_id),
        )
    ]


def _admin_en(db: Session, ctx: AuthContext, tenant_id) -> bool:
    """¿El caller administra usuarios EN ESA empresa? En la actual ya lo dijo
    require_permission; en otra, se resuelve su membresía+rol de allá."""
    if tenant_id == ctx.tenant_id:
        return ctx.is_owner or ctx.has(_WRITE)
    m = (
        db.query(Membership)
        .join(Role, Role.id == Membership.role_id)
        .filter(
            Membership.tenant_id == tenant_id,
            Membership.user_id == ctx.user_id,
            Membership.active.is_(True),
        )
        .one_or_none()
    )
    if m is None:
        return False
    if _es_owner_preset(m.role):
        return True
    from ...models import RolePermission

    return db.query(RolePermission).filter(
        RolePermission.role_id == m.role_id,
        RolePermission.permission_id == _WRITE,
    ).first() is not None


class EmpresaAccesoIn(BaseModel):
    tenant_id: UUID
    acceso: bool
    role_id: Optional[UUID] = None


@router.get("/{membership_id}/empresas")
def empresas_del_usuario(
    membership_id: UUID,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Las empresas del grupo y a cuáles entra el usuario de esta membresía."""
    m = (
        db.query(Membership)
        .filter(Membership.id == membership_id, Membership.tenant_id == ctx.tenant_id)
        .one_or_none()
    )
    if m is None:
        raise HTTPException(404, "Membership no encontrada")
    grupo = _grupo_ids(db, ctx.tenant_id)
    tenants = {t.id: t for t in db.query(Tenant).filter(Tenant.id.in_(grupo))}
    suyas = {
        mm.tenant_id: mm for mm in db.query(Membership)
        .options(joinedload(Membership.role))
        .filter(Membership.user_id == m.user_id, Membership.tenant_id.in_(grupo))
    }
    out = []
    for tid in grupo:
        t = tenants[tid]
        mm = suyas.get(tid)
        out.append({
            "tenant_id": str(tid),
            "nombre": t.trade_name or t.legal_name,
            "rfc": t.rfc,
            "es_actual": tid == ctx.tenant_id,
            "puedo_administrar": _admin_en(db, ctx, tid),
            "tiene_acceso": mm is not None and mm.active,
            "membership_id": str(mm.id) if mm else None,
            "role_id": str(mm.role_id) if mm else None,
            "role_nombre": (mm.role.nombre if mm and mm.role else None),
        })
    return {"user_email": m.user.email if m.user else None, "empresas": out}


@router.put("/{membership_id}/empresas")
def asignar_empresa(
    membership_id: UUID,
    payload: EmpresaAccesoIn,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Da o quita el acceso del usuario a UNA empresa del grupo.

    Guards: la empresa debe ser del grupo, el caller debe poder administrar
    usuarios EN ESA empresa, nadie toca sus propias membresías, y el rol OWNER
    solo lo otorga/quita un OWNER de esa empresa (anti-toma de control).
    """
    m = (
        db.query(Membership)
        .filter(Membership.id == membership_id, Membership.tenant_id == ctx.tenant_id)
        .one_or_none()
    )
    if m is None:
        raise HTTPException(404, "Membership no encontrada")
    if m.user_id == ctx.user_id:
        raise HTTPException(409, "No puedes modificar tus propios accesos")
    if payload.tenant_id not in _grupo_ids(db, ctx.tenant_id):
        raise HTTPException(422, "Esa empresa no es de tu grupo")
    if not _admin_en(db, ctx, payload.tenant_id):
        raise HTTPException(403, "No administras usuarios en esa empresa")

    existente = (
        db.query(Membership)
        .filter(Membership.tenant_id == payload.tenant_id, Membership.user_id == m.user_id)
        .one_or_none()
    )
    # ¿El caller es OWNER en la empresa DESTINO? (para los guards de OWNER)
    caller_owner_alla = (
        db.query(Membership)
        .join(Role, Role.id == Membership.role_id)
        .filter(
            Membership.tenant_id == payload.tenant_id,
            Membership.user_id == ctx.user_id,
            Membership.active.is_(True),
            Role.es_preset.is_(True), Role.nombre == "OWNER",
        ).first()
    ) is not None or (payload.tenant_id == ctx.tenant_id and ctx.is_owner)

    if not payload.acceso:
        if existente is None:
            return {"ok": True}
        rol_exist = db.query(Role).filter(Role.id == existente.role_id).one_or_none()
        if _es_owner_preset(rol_exist) and not caller_owner_alla:
            raise HTTPException(403, "Solo un OWNER de esa empresa puede quitar a un OWNER")
        db.delete(existente)
        db.commit()
        invalidate_auth_cache()
        return {"ok": True}

    role_id = payload.role_id or (existente.role_id if existente else None)
    if role_id is None:
        raise HTTPException(422, "Indica el rol con el que entra a esa empresa")
    rol = db.query(Role).filter(Role.id == role_id).one_or_none()
    if rol is None or (rol.tenant_id is not None and rol.tenant_id != payload.tenant_id):
        raise HTTPException(422, "Rol inválido para esa empresa")
    if _es_owner_preset(rol) and not caller_owner_alla:
        raise HTTPException(403, "Solo un OWNER de esa empresa puede otorgar el rol OWNER")
    if existente is not None:
        rol_previo = db.query(Role).filter(Role.id == existente.role_id).one_or_none()
        if _es_owner_preset(rol_previo) and not caller_owner_alla:
            raise HTTPException(403, "Solo un OWNER de esa empresa puede modificar a un OWNER")
        existente.role_id = role_id
        existente.active = True
    else:
        db.add(Membership(tenant_id=payload.tenant_id, user_id=m.user_id, role_id=role_id, active=True))
    db.commit()
    invalidate_auth_cache()
    return {"ok": True}
