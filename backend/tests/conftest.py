"""Test fixtures.

We set the minimum env BEFORE importing the app so settings validate. The
engine is created lazily (no connection at import), so these API-level tests
need no live database.
"""
import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/test"
)
os.environ.setdefault("AUTH_CACHE_TTL", "0")  # sin caché de auth en tests (determinismo)
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault(
    "SUPABASE_JWKS_URL",
    "https://example.supabase.co/auth/v1/.well-known/jwks.json",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="session")
def db_engine():
    """The app engine, but only if a live database is reachable.

    DB-backed tests (RLS, RBAC) require `alembic upgrade head` to have run.
    When no DB is available (e.g. a bare local run pointed at a stub URL),
    these tests skip instead of erroring.
    """
    from app.core.db import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            # Confirm the schema is migrated, not just reachable.
            conn.execute(text("SELECT 1 FROM permissions LIMIT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"No migrated database available: {exc}")
    return engine


def crear_sucursal(db, *, tenant_id, cliente_id=None, nombre, serie_factura_id=None,
                   serie_remision_id=None, **kw):
    """Alta de plaza + vínculo en un paso (lo que antes era una Sucursal con
    dueño). Los tests que digan "la sucursal del cliente X" pasan por aquí."""
    from app.models import ClienteSucursal, Sucursal

    s = Sucursal(tenant_id=tenant_id, nombre=nombre, **kw)
    db.add(s)
    db.flush()
    if cliente_id is not None:
        db.add(ClienteSucursal(
            tenant_id=tenant_id, cliente_id=cliente_id, sucursal_id=s.id,
            serie_factura_id=serie_factura_id, serie_remision_id=serie_remision_id,
        ))
        db.flush()
    return s
