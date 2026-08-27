"""Auth endpoints.

`/me` resolves the full server-side authorization context behind a bearer
token: who the user is, which tenants they belong to, the tenant the request
is scoped to, and the effective permission set. The frontend uses this to
build the menu and gate UI actions — but every backend write is independently
gated by `require_permission`, never by what the client claims.
"""
from fastapi import APIRouter, Depends

from ...core.rbac import AuthContext, get_auth_context

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
def me(ctx: AuthContext = Depends(get_auth_context)) -> dict:
    """Return the verified identity + resolved tenant/role/permissions."""
    return {
        "auth_user_id": ctx.auth_user_id,
        "user_id": str(ctx.user_id),
        "email": ctx.email,
        "active_tenant": {
            "tenant_id": str(ctx.tenant_id),
            "role": ctx.role_name,
            "is_owner": ctx.is_owner,
        },
        "tenants": [
            {
                "tenant_id": str(m.tenant_id),
                "slug": m.slug,
                "name": m.name,
                "role": m.role_name,
                "color": m.color,
                "rfc": m.rfc,
            }
            for m in ctx.memberships
        ],
        "permissions": sorted(ctx.permissions),
    }
