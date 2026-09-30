"""Auth endpoints.

`/me` resolves the full server-side authorization context behind a bearer
token: who the user is, which tenants they belong to, the tenant the request
is scoped to, and the effective permission set. The frontend uses this to
build the menu and gate UI actions — but every backend write is independently
gated by `require_permission`, never by what the client claims.
"""
from fastapi import APIRouter, Depends

from ...core.rbac import AuthContext, es_duenio_de_sae, get_auth_context
from ...services import sae_escritura

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
            # La pantalla enseña «Dar de alta en SAE» sólo aquí: la cola de
            # SAE contesta 403 a cualquier otro tenant (require_duenio_de_sae).
            "sae_conectado": es_duenio_de_sae(ctx.tenant_id),
            # Quién escribe hoy la cola: el Facturador con su reloj encendido,
            # o el conector del bot mientras siga apagado.
            "sae_escritor": "FACTURADOR" if sae_escritura.activo() else "BOT",
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
