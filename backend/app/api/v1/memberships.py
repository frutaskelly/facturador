"""Memberships — list members of the tenant and manage their role/status.

Reads gated by `menu:ajustes.usuarios`; writes by `membership:gestionar`.

RLS scopes every query to the current tenant. You can reassign a member to any
preset role or one of your own custom roles, activate/deactivate them, or remove
them. You cannot touch your own membership (prevents self-lockout). Inviting
brand-new users is an operator/provisioning flow, not exposed here.
"""
from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
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
    ctx: AuthContext = Depends(require_permission(_WRITE)),
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

    m = Membership(tenant_id=ctx.tenant_id, user_id=user.id, role_id=payload.role_id, active=True)
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
