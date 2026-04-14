"""
PostgreSQL connection management via SQLAlchemy.

All connections used inside a request context are tenant-scoped —
the database enforces row-level security via the app.tenant_id session variable.
"""
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from db.config import get_secret

_engine: Engine | None = None


def _build_url() -> str:
    # DATABASE_URL env var takes priority (local dev via Auth Proxy)
    import os
    if url := os.environ.get("DATABASE_URL"):
        return url
    base = get_secret("db-connection-string")
    password = get_secret("db-app-user-password")
    # base format: postgresql+psycopg2://app_user@host:port/dbname
    return base.replace("app_user@", f"app_user:{password}@", 1)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            _build_url(),
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return _engine


@contextmanager
def get_db(tenant_id: str | None = None):
    """Yield a SQLAlchemy connection with optional tenant context set.

    Usage:
        with get_db(tenant_id=g.user["tenant_id"]) as conn:
            rows = conn.execute(text("SELECT * FROM positions")).mappings().all()
    """
    with get_engine().connect() as conn:
        if tenant_id:
            conn.execute(
                text("SET LOCAL app.tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
