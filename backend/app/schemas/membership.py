"""Schemas for memberships (user ↔ tenant ↔ role).

v1 admin scope: list members, change a member's role, activate/deactivate, and
remove a member. Creating brand-new users (invites/provisioning) is an operator
flow, not exposed here — under RLS a user with no membership in this tenant is
invisible, and account creation is out of scope.
"""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .common import ORMModel


class MembershipUpdate(BaseModel):
    role_id: Optional[uuid.UUID] = None
    active: Optional[bool] = None
    acceso_todas_sucursales: Optional[bool] = None
    # Candado por cliente (portal): [] o None = sin límite. Mandarlo REEMPLAZA
    # el candado completo (no es un merge).
    cliente_scope: Optional[list[uuid.UUID]] = None


class CrearUsuarioIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    full_name: Optional[str] = Field(default=None, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    role_id: uuid.UUID
    cliente_scope: Optional[list[uuid.UUID]] = None


class CambiarPasswordIn(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class MembershipOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role_id: uuid.UUID
    active: bool
    acceso_todas_sucursales: bool
    cliente_scope: Optional[list[uuid.UUID]] = None
    created_at: datetime
    updated_at: datetime
    # joined display fields (populated by the router)
    user_email: Optional[str] = None
    user_full_name: Optional[str] = None
    role_nombre: Optional[str] = None
