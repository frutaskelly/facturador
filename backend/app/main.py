"""SmartSupply v2.0 — FastAPI entry point."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .core.config import settings
from .services.facturama import startup_warnings as facturama_startup_warnings
from .api.v1 import (
    almacenes,
    auth,
    categorias,
    clientes,
    cobranza,
    conversiones,
    correo,
    empresa,
    esquemas_impuesto,
    facturas,
    inventario,
    listas_precios,
    memberships,
    ordenes_compra,
    permissions,
    platform,
    pos,
    precios,
    productos,
    proveedores,
    registro,
    remisiones,
    roles,
    sat,
    series,
    sucursales,
)

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    facturama_env = (
        "producción" if "apisandbox.facturama.mx" not in settings.FACTURAMA_BASE_URL
        else "sandbox"
    )
    log.info(
        "SmartSupply v2.0 starting — env=%s, facturama=%s, origins=%s",
        settings.ENVIRONMENT,
        facturama_env,
        settings.allowed_origins_list(),
    )
    for w in facturama_startup_warnings(settings):
        log.warning("Facturama config: %s", w)
    yield
    log.info("Shutting down")


app = FastAPI(
    title="SmartSupply v2.0",
    version=__version__,
    description="Plataforma SaaS multi-tenant — cadena de suministro gobierno-alimentos.",
    lifespan=lifespan,
    # En producción no se publica la superficie completa del API (docs/openapi).
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Request-Id", "X-Total-Count"],
    max_age=3600,
)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "version": __version__, "env": settings.ENVIRONMENT}


@app.get("/api", tags=["meta"])
def api_root() -> dict:
    return {"service": "smartsupply-v2", "docs": "/docs", "health": "/health"}


# ─── API v1 routers ───────────────────────────────────────────────────────────
app.include_router(auth.router, prefix="/api/v1")
app.include_router(registro.router, prefix="/api/v1")  # PÚBLICO (signup autoservicio)
# Phase 3 — catálogo / master data
app.include_router(categorias.router, prefix="/api/v1")
app.include_router(esquemas_impuesto.router, prefix="/api/v1")
app.include_router(productos.router, prefix="/api/v1")
app.include_router(listas_precios.router, prefix="/api/v1")
app.include_router(clientes.router, prefix="/api/v1")
app.include_router(cobranza.router, prefix="/api/v1")
# Phase 4 — operaciones
app.include_router(proveedores.router, prefix="/api/v1")
app.include_router(almacenes.router, prefix="/api/v1")
app.include_router(inventario.router, prefix="/api/v1")
app.include_router(ordenes_compra.router, prefix="/api/v1")
app.include_router(conversiones.router, prefix="/api/v1")
app.include_router(remisiones.router, prefix="/api/v1")
app.include_router(facturas.router, prefix="/api/v1")
app.include_router(sat.router, prefix="/api/v1")
# IAM admin — roles, permission catalog, memberships
app.include_router(roles.router, prefix="/api/v1")
app.include_router(permissions.router, prefix="/api/v1")
app.include_router(memberships.router, prefix="/api/v1")
# precios v2 — sucursales + cotización + overrides
app.include_router(sucursales.router, prefix="/api/v1")
app.include_router(precios.router, prefix="/api/v1")
# series de folios
app.include_router(series.router, prefix="/api/v1")
# correo SMTP del tenant
app.include_router(correo.router, prefix="/api/v1")
# panel de operador de plataforma — cross-tenant, read-only, allowlist
app.include_router(platform.router, prefix="/api/v1")
# empresa / emisor — datos fiscales del tenant + CSD
app.include_router(empresa.router, prefix="/api/v1")
app.include_router(pos.router, prefix="/api/v1")
