"""Roles — list preset + custom roles, and manage custom roles + their perms.

Reads gated by `menu:ajustes.roles`; writes by `role:gestionar`.

Preset roles (es_preset=true, tenant_id NULL) are GLOBAL and shared by every
tenant, so they are strictly read-only here — any mutation is rejected (403).
Only a tenant's own custom roles (tenant_id = current tenant) can be created,
edited, deleted, or have their permission set changed. RLS already limits what
this session can see (own roles + presets); the es_preset guard prevents writing
through to the shared presets.
"""
from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, invalidate_auth_cache, require_permission
from ...models import Membership, Permission, Role, RolePermission
from ...schemas.role import (
    RoleCreate,
    RoleDetailOut,
    RoleOut,
    RolePermissionsIn,
    RoleUpdate,
)
from ._helpers import flush_or_conflict, get_or_404

router = APIRouter(prefix="/roles", tags=["roles"])

_READ = "menu:ajustes.roles"
_WRITE = "role:gestionar"
_DUP = "Ya existe un rol con ese nombre"


def _perms_for(db: Session, role_id: UUID) -> List[str]:
    return [
        pid for (pid,) in db.query(RolePermission.permission_id)
        .filter(RolePermission.role_id == role_id)
        .all()
    ]


def _detail(db: Session, role: Role) -> RoleDetailOut:
    out = RoleDetailOut.model_validate(role)
    out.permissions = _perms_for(db, role.id)
    return out


def _editable_or_403(role: Role, ctx: AuthContext) -> None:
    """Preset roles are global/shared → never writable. Belt-and-suspenders with
    RLS: a fetched role is either preset (tenant_id NULL) or the caller's own."""
    if role.es_preset or role.tenant_id != ctx.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Los roles predefinidos no se pueden modificar",
        )


def _set_permissions(db: Session, role_id: UUID, perms: List[str]) -> None:
    """Replace the role's permission set with `perms`, validating each id against
    the catalog. Order-preserving, de-duplicated."""
    wanted = list(dict.fromkeys(perms))
    if wanted:
        valid = {
            pid for (pid,) in db.query(Permission.id).filter(Permission.id.in_(wanted)).all()
        }
        unknown = [p for p in wanted if p not in valid]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Permisos desconocidos: {', '.join(unknown)}",
            )
    db.query(RolePermission).filter(RolePermission.role_id == role_id).delete()
    for pid in wanted:
        db.add(RolePermission(role_id=role_id, permission_id=pid))
    db.flush()


@router.get("", response_model=List[RoleOut])
def list_roles(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    # RLS already scopes to: this tenant's custom roles + all preset roles.
    rows = db.query(Role).order_by(Role.es_preset.desc(), Role.nombre).all()
    return [RoleOut.model_validate(r) for r in rows]


@router.get("/{role_id}", response_model=RoleDetailOut)
def get_role(
    role_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return _detail(db, get_or_404(db, Role, role_id))


@router.post("", response_model=RoleDetailOut, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    role = Role(
        tenant_id=ctx.tenant_id,
        nombre=payload.nombre.strip(),
        vertical=payload.vertical,
        descripcion=payload.descripcion,
        es_preset=False,
    )
    db.add(role)
    flush_or_conflict(db, detail=_DUP)
    _set_permissions(db, role.id, payload.permissions)
    db.refresh(role)
    return _detail(db, role)


@router.patch("/{role_id}", response_model=RoleDetailOut)
def update_role(
    role_id: UUID,
    payload: RoleUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    role = get_or_404(db, Role, role_id)
    _editable_or_403(role, ctx)
    data = payload.model_dump(exclude_unset=True)
    perms = data.pop("permissions", None)
    if "nombre" in data and data["nombre"]:
        data["nombre"] = data["nombre"].strip()
    for key, value in data.items():
        setattr(role, key, value)
    flush_or_conflict(db, detail=_DUP)
    if perms is not None:
        _set_permissions(db, role.id, perms)
        invalidate_auth_cache()  # permisos del rol cambiaron → refresca caché
    db.refresh(role)
    return _detail(db, role)


@router.put("/{role_id}/permissions", response_model=RoleDetailOut)
def set_role_permissions(
    role_id: UUID,
    payload: RolePermissionsIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    role = get_or_404(db, Role, role_id)
    _editable_or_403(role, ctx)
    _set_permissions(db, role.id, payload.permissions)
    invalidate_auth_cache()  # permisos del rol cambiaron → refresca caché
    db.refresh(role)
    return _detail(db, role)


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("role:eliminar")),
):
    role = get_or_404(db, Role, role_id)
    _editable_or_403(role, ctx)
    if db.query(Membership.id).filter(Membership.role_id == role_id).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El rol está asignado a uno o más usuarios; reasígnalos antes de eliminarlo",
        )
    db.query(RolePermission).filter(RolePermission.role_id == role_id).delete()
    db.delete(role)
    db.flush()
    return None
