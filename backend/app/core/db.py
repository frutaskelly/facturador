"""Database engine + session factories.

RLS model (v2):
  - Every table with tenant data carries `tenant_id` and an RLS policy keyed on
    `current_setting('app.current_tenant_id')`.
  - The backend connects as a NON-superuser role (DB_APP_ROLE) so RLS is
    actually enforced (Supabase's `postgres` role has BYPASSRLS).
  - `set_tenant()` sets the GUC from a tenant_id that was derived from the
    JWT-validated membership — NEVER from a client-supplied header.
"""
from contextlib import contextmanager
from typing import Iterator
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from .config import settings

# ─── Sync engine (request handlers) ──────────────────────────────────────────
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=Session)

Base = declarative_base()


def set_tenant(db: Session, tenant_id: UUID | str) -> None:
    """Scope the session to a tenant for RLS. tenant_id MUST be validated."""
    db.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )


def set_role_tenant(db: Session, tenant_id: UUID | str) -> None:
    """Baja a `app_user` y fija el tenant en UN solo viaje a la base.

    `set_config('role', 'app_user', true)` equivale a `SET LOCAL ROLE app_user`
    (ambos fijan el GUC `role` dentro de la transacción); combinarlo con el GUC
    del tenant en un solo SELECT ahorra una ida y vuelta por request — relevante
    cuando la base está en la nube."""
    db.execute(
        text(
            "SELECT set_config('role', 'app_user', true), "
            "set_config('app.current_tenant_id', :tid, true)"
        ),
        {"tid": str(tenant_id)},
    )


def clear_tenant(db: Session) -> None:
    db.execute(text("SELECT set_config('app.current_tenant_id', '', true)"))


def get_db() -> Iterator[Session]:
    """Plain session, no tenant scope (auth lookups, platform operator)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts / background tasks."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
