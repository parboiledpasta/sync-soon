"""
SQLAlchemy ORM Models and Session Factory for Semabridge.

Provides a unified data-access layer that works with:
    • DuckDB   (default, embedded, zero-config)
    • PostgreSQL (optional, for distributed Temporal worker clusters)

The active backend is selected via the ``DATABASE_URL`` environment variable.
When unset, a local DuckDB file (``semabridge_orm.duckdb``) is used.

Tables:
    snapshots       – point-in-time SML model snapshots
    versions        – version tags and metadata
    sync_runs       – audit trail of orchestration runs

Usage:
    from semabridge.storage.orm import get_session, SnapshotRow

    with get_session() as session:
        row = SnapshotRow(...)
        session.add(row)
        session.commit()
"""

from __future__ import annotations

import os
import uuid as _uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    sessionmaker,
)


def _new_uuid() -> str:
    return str(_uuid.uuid4())


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class SnapshotRow(Base):
    """Point-in-time SML snapshot persisted after every successful extraction."""

    __tablename__ = "snapshots"

    snapshot_id = Column(String(36), primary_key=True, default=_new_uuid)
    tenant_id = Column(String(128), nullable=False, index=True)
    model_id = Column(String(256), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    version_tag = Column(String(64), nullable=True)
    sml_blob = Column(Text, nullable=True)  # JSON text (JSONB on Postgres via raw SQL if needed)
    status = Column(String(32), nullable=False, default="success")
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    initiated_by = Column(String(32), nullable=False, default="temporal")
    run_id = Column(String(36), nullable=True)

    __table_args__ = (
        Index("ix_snap_tenant_model", "tenant_id", "model_id"),
        Index("ix_snap_timestamp", "timestamp"),
    )


class VersionRow(Base):
    """Tracks named version tags applied to model snapshots."""

    __tablename__ = "versions"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    version_tag = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False)
    model_id = Column(String(256), nullable=False)
    snapshot_id = Column(String(36), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    notes = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_ver_tenant_model", "tenant_id", "model_id"),
    )


class SyncRunRow(Base):
    """Audit trail for orchestration runs (both local and Temporal)."""

    __tablename__ = "sync_runs"

    run_id = Column(String(36), primary_key=True, default=_new_uuid)
    tenant_id = Column(String(128), nullable=False)
    model_id = Column(String(256), nullable=True)
    started_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), nullable=False, default="running")
    phase = Column(String(32), nullable=True)
    changes_detected = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    orchestrator = Column(String(32), nullable=False, default="local")  # local | temporal


# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------

_engine = None
_SessionFactory: Optional[sessionmaker] = None


def _default_database_url() -> str:
    """
    Build the default DuckDB connection URL.

    Falls back to a file next to the legacy ``semabridge.db``.
    """
    try:
        from semabridge.core.settings import get_settings
        settings = get_settings()
        repo_path = Path(settings.core.repository_path).expanduser().resolve()
        db_path = repo_path.parent / "semabridge_orm.duckdb"
    except Exception:
        db_path = Path(__file__).resolve().parent.parent.parent.parent / "semabridge_orm.duckdb"
    return f"duckdb:///{db_path}"


def get_engine(database_url: Optional[str] = None):
    """
    Return a singleton SQLAlchemy engine.

    Priority:
        1. Explicit *database_url* argument
        2. ``DATABASE_URL`` environment variable
        3. Local DuckDB file (default)
    """
    global _engine
    if _engine is not None:
        return _engine

    url = database_url or os.getenv("DATABASE_URL") or _default_database_url()

    # Engine kwargs differ by backend
    kwargs: dict[str, Any] = {}
    if url.startswith("duckdb"):
        kwargs["echo"] = False
    elif url.startswith("postgresql"):
        kwargs.update(
            pool_size=20,
            max_overflow=40,
            pool_pre_ping=True,
            echo=False,
        )
    else:
        kwargs["echo"] = False

    _engine = create_engine(url, **kwargs)

    # Create tables if they do not exist
    Base.metadata.create_all(_engine)

    return _engine


def _get_session_factory() -> sessionmaker:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def get_session(database_url: Optional[str] = None) -> Generator[Session, None, None]:
    """
    Context-managed ORM session.

    Usage:
        with get_session() as session:
            session.add(row)
            session.commit()
    """
    if database_url:
        # One-off engine for explicit URL (tests, migrations)
        eng = create_engine(database_url)
        Base.metadata.create_all(eng)
        factory = sessionmaker(bind=eng, expire_on_commit=False)
    else:
        factory = _get_session_factory()

    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Reset helpers (tests)
# ---------------------------------------------------------------------------


def reset_engine() -> None:
    """Drop the cached engine so the next call to ``get_engine`` creates a fresh one."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
