"""Tenants, users, memberships — the identity backbone.

Security model (v2):
  - A `User` is global and is linked to Supabase Auth via `auth_user_id`
    (the verified JWT `sub`). It carries NO tenant authority by itself.
  - A `Membership` is the ONLY thing that grants a user access to a tenant,
    and it points at exactly one `Role`. Tenant + role are always resolved
    server-side from an active membership — never from a request header.
  - v1's legacy `role` enum on memberships is gone. Authorization is RBAC
    via `role_id` only.
"""
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import deferred, relationship

from ..core.db import Base
from .base import SoftDeleteMixin, TimestampMixin, uuid_pk

TENANT_TIER = Enum("PRINCIPAL", "SUB", "SUB_SUB", name="tenant_tier")
TENANT_STATUS = Enum("ACTIVE", "TRIAL", "SUSPENDED", "CHURNED", name="tenant_status")


class Tenant(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tenants"

    id = uuid_pk()
    parent_tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True, index=True
    )
    tier = Column(TENANT_TIER, nullable=False, server_default="PRINCIPAL")
    status = Column(TENANT_STATUS, nullable=False, server_default="TRIAL")
    slug = Column(String(50), unique=True, nullable=False)
    legal_name = Column(String(254), nullable=False)
    trade_name = Column(String(254))
    rfc = Column(String(15), unique=True, nullable=False)
    regimen_fiscal_sat = Column(String(4), nullable=False)
    domicilio_fiscal_cp = Column(String(5), nullable=False)
    domicilio_fiscal = Column(JSONB, nullable=False, server_default="{}")
    config = Column(JSONB, nullable=False, server_default="{}")
    # Logo del emisor para la representación impresa (subido en Ajustes › Empresa).
    # deferred: el BYTEA (hasta 2 MB) NO se carga en cada SELECT de tenants —
    # rbac resuelve la fila del tenant en cada request autenticado.
    logo = deferred(Column(LargeBinary))
    logo_mime = Column(String(50))
    plan = Column(String(50), nullable=False, server_default="trial")
    seats_limit = Column(Integer, nullable=False, server_default="3")
    trial_ends_at = Column(Date)

    parent = relationship("Tenant", remote_side="Tenant.id")
    memberships = relationship("Membership", back_populates="tenant")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id = uuid_pk()
    email = Column(String(254), unique=True, nullable=False, index=True)
    full_name = Column(String(254))
    phone = Column(String(20))
    auth_provider = Column(String(20), nullable=False, server_default="supabase")
    # The Supabase Auth user id (JWT `sub`). Set on first login if the user was
    # provisioned by email beforehand. Unique once present.
    auth_user_id = Column(String(254), unique=True, index=True)

    memberships = relationship("Membership", back_populates="user")


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),)

    id = uuid_pk()
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # RBAC: the single source of authorization for this (user, tenant) pair.
    role_id = Column(
        UUID(as_uuid=True), ForeignKey("roles.id"), nullable=False, index=True
    )
    acceso_todas_sucursales = Column(
        Boolean, nullable=False, server_default="false"
    )
    active = Column(Boolean, nullable=False, server_default="true")

    tenant = relationship("Tenant", back_populates="memberships")
    user = relationship("User", back_populates="memberships")
    role = relationship("Role")
