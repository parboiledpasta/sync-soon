"""
Metadata caching for incremental processing.

Enables fast re-runs by only processing changed tables.
Uses SQLite for reliable local storage.

Key improvements over semantic-sync:
1. Hash-based change detection (not just timestamps)
2. Atomic writes to prevent corruption
3. Schema versioning for cache invalidation
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Optional

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

CACHE_SCHEMA_VERSION = 1


class MetadataCache:
    """
    SQLite-based cache for incremental metadata processing.
    
    Tracks:
    - Table metadata hashes for change detection
    - Last sync timestamps
    - Extraction state for resume capability
    """
    
    def __init__(self, cache_dir: str | Path = ".semabridge_cache"):
        """
        Initialize the metadata cache.
        
        Args:
            cache_dir: Directory to store cache files
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.cache_dir / "metadata_cache.db"
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize the SQLite database schema."""
        with self._connection() as conn:
            # Check schema version
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            result = conn.execute(
                "SELECT value FROM cache_meta WHERE key = 'schema_version'"
            ).fetchone()
            
            current_version = int(result[0]) if result else 0
            
            if current_version < CACHE_SCHEMA_VERSION:
                # Reset cache on schema change
                logger.info(f"Cache schema upgrade: {current_version} -> {CACHE_SCHEMA_VERSION}")
                conn.execute("DROP TABLE IF EXISTS table_hashes")
                conn.execute("DROP TABLE IF EXISTS sync_history")
                conn.execute("""
                    INSERT OR REPLACE INTO cache_meta (key, value) 
                    VALUES ('schema_version', ?)
                """, (str(CACHE_SCHEMA_VERSION),))
            
            # Create tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS table_hashes (
                    database TEXT,
                    schema TEXT,
                    table_name TEXT,
                    column_hash TEXT,
                    row_count INTEGER,
                    last_altered TEXT,
                    cached_at TEXT,
                    PRIMARY KEY (database, schema, table_name)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_history (
                    sync_id TEXT PRIMARY KEY,
                    started_at TEXT,
                    completed_at TEXT,
                    status TEXT,
                    tables_processed INTEGER,
                    tables_changed INTEGER,
                    model_name TEXT,
                    details TEXT
                )
            """)
            
            conn.commit()
    
    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database connections."""
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def get_table_hash(
        self,
        database: str,
        schema: str,
        table_name: str,
    ) -> Optional[str]:
        """
        Get the cached hash for a table.
        
        Args:
            database: Database name
            schema: Schema name
            table_name: Table name
            
        Returns:
            Cached hash or None if not found
        """
        with self._connection() as conn:
            result = conn.execute("""
                SELECT column_hash FROM table_hashes
                WHERE database = ? AND schema = ? AND table_name = ?
            """, (database.upper(), schema.upper(), table_name.upper())).fetchone()
            
            return result["column_hash"] if result else None
    
    def set_table_hash(
        self,
        database: str,
        schema: str,
        table_name: str,
        column_hash: str,
        row_count: int = 0,
        last_altered: Optional[str] = None,
    ) -> None:
        """
        Store the hash for a table.
        
        Args:
            database: Database name
            schema: Schema name
            table_name: Table name
            column_hash: Hash of column metadata
            row_count: Optional row count
            last_altered: Optional last altered timestamp
        """
        with self._connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO table_hashes 
                (database, schema, table_name, column_hash, row_count, last_altered, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                database.upper(),
                schema.upper(),
                table_name.upper(),
                column_hash,
                row_count,
                last_altered,
                datetime.utcnow().isoformat(),
            ))
            conn.commit()
    
    def get_changed_tables(
        self,
        current_tables: dict[str, dict[str, Any]],
        database: str,
        schema: str,
    ) -> tuple[list[str], list[str], list[str]]:
        """
        Detect changed, added, and removed tables.
        
        Args:
            current_tables: Dict of table_name -> {columns, row_count, last_altered}
            database: Database name
            schema: Schema name
            
        Returns:
            Tuple of (changed, added, removed) table lists
        """
        changed: list[str] = []
        added: list[str] = []
        removed: list[str] = []
        
        # Get all cached tables
        with self._connection() as conn:
            cached = {
                row["table_name"]: row["column_hash"]
                for row in conn.execute("""
                    SELECT table_name, column_hash FROM table_hashes
                    WHERE database = ? AND schema = ?
                """, (database.upper(), schema.upper())).fetchall()
            }
        
        current_names = set(t.upper() for t in current_tables.keys())
        cached_names = set(cached.keys())
        
        # Find added tables
        added = list(current_names - cached_names)
        
        # Find removed tables
        removed = list(cached_names - current_names)
        
        # Find changed tables
        for table_name, metadata in current_tables.items():
            table_upper = table_name.upper()
            if table_upper in cached:
                current_hash = self._compute_hash(metadata.get("columns", []))
                if current_hash != cached[table_upper]:
                    changed.append(table_name)
        
        return changed, added, removed
    
    def _compute_hash(self, columns: list[dict[str, Any]]) -> str:
        """Compute a hash of column metadata for change detection."""
        # Sort columns by name for consistent hashing
        sorted_cols = sorted(columns, key=lambda c: c.get("name", ""))
        content = json.dumps(sorted_cols, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def record_sync(
        self,
        sync_id: str,
        status: str,
        tables_processed: int,
        tables_changed: int,
        model_name: str,
        details: Optional[dict] = None,
    ) -> None:
        """Record a sync operation in history."""
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO sync_history 
                (sync_id, started_at, completed_at, status, tables_processed, tables_changed, model_name, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sync_id,
                datetime.utcnow().isoformat(),
                datetime.utcnow().isoformat(),
                status,
                tables_processed,
                tables_changed,
                model_name,
                json.dumps(details) if details else None,
            ))
            conn.commit()
    
    def get_last_sync(self, model_name: Optional[str] = None) -> Optional[dict]:
        """Get the most recent sync operation."""
        with self._connection() as conn:
            if model_name:
                result = conn.execute("""
                    SELECT * FROM sync_history 
                    WHERE model_name = ?
                    ORDER BY completed_at DESC LIMIT 1
                """, (model_name,)).fetchone()
            else:
                result = conn.execute("""
                    SELECT * FROM sync_history 
                    ORDER BY completed_at DESC LIMIT 1
                """).fetchone()
            
            if result:
                return dict(result)
            return None
    
    def clear(self) -> None:
        """Clear all cached data."""
        with self._connection() as conn:
            conn.execute("DELETE FROM table_hashes")
            conn.execute("DELETE FROM sync_history")
            conn.commit()
        logger.info("Cache cleared")
