"""
DuckDB-based Version Control System.

Manages semantic model history using DuckDB as an embedded metadata engine.
Treats SML JSON as versioned data, allowing diffs, rollbacks, and time-travel.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import duckdb
from pydantic import BaseModel

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class Snapshot(BaseModel):
    """A point-in-time snapshot of a semantic model."""
    snapshot_id: str
    project_id: str
    timestamp: str
    version_tag: Optional[str]
    sml_blob: Dict[str, Any]
    status: str = "success"  # success, failed, pending
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    initiated_by: str = "cli"  # cli, api, scheduled
    run_id: Optional[str] = None


class ModelChange(BaseModel):
    """A granular change record."""
    object_type: str
    object_name: str
    diff_type: str  # ADDED, MODIFIED, DELETED
    old_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None


class DuckDBManager:
    """
    Manages semantic model state in DuckDB.
    
    Uses a single persistent connection with thread-safe write locking
    to prevent lock conflicts in concurrent deployments.
    """
    
    def __init__(self, db_path: str = None):
        # Use absolute path to ensure consistent DB location
        if db_path is None:
            try:
                from semabridge.core.settings import get_settings
                settings = get_settings()
                db_path = str(Path(settings.core.repository_path).expanduser().resolve())
            except Exception:
                import sys
                from pathlib import Path
                
                if getattr(sys, 'frozen', False):
                    # Running as compiled exe - use executable directory
                    project_root = Path(sys.executable).parent
                else:
                    # Running from source - use project root
                    project_root = Path(__file__).resolve().parent.parent.parent.parent
                
                db_path = str(project_root / "semabridge.db")
        self.db_path = db_path
        self._write_lock = threading.Lock()
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._conn_lock = threading.Lock()
        self._init_db()
    
    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create a persistent database connection with retry.

        Uses a single persistent connection to prevent DuckDB file lock
        conflicts.  Thread-safe via ``_conn_lock``.  Falls back to
        creating a new connection with retries if the persistent one is
        stale or closed.
        """
        with self._conn_lock:
            if self._conn is not None:
                try:
                    # Quick health check — if this raises, connection is stale
                    self._conn.execute("SELECT 1")
                    return self._conn
                except Exception:
                    logger.debug("Persistent DuckDB connection stale — reconnecting")
                    try:
                        self._conn.close()
                    except Exception:
                        pass
                    self._conn = None

            max_retries = 5
            base_delay = 0.3
            for attempt in range(1, max_retries + 1):
                try:
                    conn = duckdb.connect(self.db_path)
                    self._conn = conn
                    return conn
                except (IOError, Exception) as exc:
                    msg = str(exc).lower()
                    lock_patterns = (
                        "lock", "used by another", "busy", "attach",
                        "conflict", "handle", "io error",
                        "could not set lock",
                    )
                    if any(p in msg for p in lock_patterns) and attempt < max_retries:
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.warning(
                            f"DuckDB connect attempt {attempt}/{max_retries} "
                            f"failed (lock contention), retrying in "
                            f"{delay:.1f}s: {exc}"
                        )
                        time.sleep(delay)
                    else:
                        raise

    def close(self) -> None:
        """Close the persistent DuckDB connection.
        
        Should be called during application shutdown or between
        isolated test runs.
        """
        with self._conn_lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception as exc:
                    logger.debug(f"Error closing DuckDB connection: {exc}")
                finally:
                    self._conn = None
    
    def __del__(self):
        """Ensure connection is closed on garbage collection."""
        try:
            self.close()
        except Exception:
            pass
    
    def _init_db(self) -> None:
        """Initialize database schema."""
        conn = self._get_connection()
        conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    workspace_id VARCHAR,
                    adapter VARCHAR,
                    source_connection VARCHAR,
                    last_updated TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id VARCHAR PRIMARY KEY,
                    project_id VARCHAR NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    version_tag VARCHAR,
                    sml_blob JSON,
                    status VARCHAR DEFAULT 'success',
                    duration_ms INTEGER,
                    error_message VARCHAR,
                    initiated_by VARCHAR DEFAULT 'cli',
                    run_id VARCHAR,
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );
                
                CREATE TABLE IF NOT EXISTS changes (
                    change_id VARCHAR PRIMARY KEY,
                    snapshot_id VARCHAR NOT NULL,
                    object_type VARCHAR,
                    object_name VARCHAR,
                    diff_type VARCHAR,
                    old_value JSON,
                    new_value JSON,
                    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
                );
                
                -- Execution Run tracking (Step 2, 10)
                CREATE TABLE IF NOT EXISTS runs (
                    run_id VARCHAR PRIMARY KEY,
                    project_id VARCHAR NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP,
                    status VARCHAR NOT NULL DEFAULT 'running',
                    final_step INTEGER,
                    source_type VARCHAR,
                    target_type VARCHAR,
                    error_message VARCHAR,
                    duration_ms INTEGER
                );
                
                -- Source Format artifacts (Step 4, 7)
                CREATE TABLE IF NOT EXISTS source_artifacts (
                    artifact_id VARCHAR PRIMARY KEY,
                    run_id VARCHAR NOT NULL,
                    source_type VARCHAR NOT NULL,
                    content_json JSON NOT NULL,
                    created_at TIMESTAMP NOT NULL
                );
        """)

        # Migrations for existing tables
        # projects table
        cols = [c[1] for c in conn.execute("PRAGMA table_info('projects')").fetchall()]
        if 'adapter' not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN adapter VARCHAR")
        if 'source_connection' not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN source_connection VARCHAR")

        # snapshots table
        cols = [c[1] for c in conn.execute("PRAGMA table_info('snapshots')").fetchall()]
        if 'status' not in cols:
            conn.execute("ALTER TABLE snapshots ADD COLUMN status VARCHAR DEFAULT 'success'")
        if 'duration_ms' not in cols:
            conn.execute("ALTER TABLE snapshots ADD COLUMN duration_ms INTEGER")
        if 'error_message' not in cols:
            conn.execute("ALTER TABLE snapshots ADD COLUMN error_message VARCHAR")
        if 'initiated_by' not in cols:
            conn.execute("ALTER TABLE snapshots ADD COLUMN initiated_by VARCHAR DEFAULT 'cli'")
        if 'run_id' not in cols:
            conn.execute("ALTER TABLE snapshots ADD COLUMN run_id VARCHAR")
    
    def ensure_project(self, project_id: str, name: str, workspace_id: str, adapter: str = "fabric", source_connection: str = None) -> None:
        """Ensure project exists."""
        conn = self._get_connection()
        now = datetime.utcnow()
        conn.execute("""
                INSERT INTO projects (project_id, name, workspace_id, adapter, source_connection, last_updated)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id) DO UPDATE SET 
                    name = excluded.name,
                    adapter = excluded.adapter,
                    source_connection = excluded.source_connection,
                    last_updated = excluded.last_updated
            """, [project_id, name, workspace_id, adapter, source_connection, now])
    
    def list_projects(self) -> List[Dict[str, Any]]:
        """List all tracked projects."""
        conn = self._get_connection()
        results = conn.execute("""
            SELECT project_id, name, workspace_id, adapter, last_updated 
            FROM projects 
            ORDER BY last_updated DESC
        """).fetchall()
        return [
            {
                "id": r[0],
                "displayName": r[1],
                "workspace_id": r[2],
                "adapter": r[3],
                "last_updated": str(r[4])
            }
            for r in results
        ]

    def get_head(self, project_id: str) -> Optional[Snapshot]:
        """Get the latest snapshot for a project."""
        conn = self._get_connection()
        result = conn.execute("""
            SELECT snapshot_id, project_id, timestamp, version_tag, sml_blob,
                   status, duration_ms, error_message, initiated_by, run_id
            FROM snapshots
            WHERE project_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, [project_id]).fetchone()
        if not result:
            return None
        return Snapshot(
            snapshot_id=result[0],
            project_id=result[1],
            timestamp=str(result[2]),
            version_tag=result[3],
            sml_blob=json.loads(result[4]),
            status=result[5],
            duration_ms=result[6],
            error_message=result[7],
            initiated_by=result[8],
            run_id=result[9]
        )

    def commit_model(self, project_id: str, sml_json: Dict[str, Any], tag: Optional[str] = None,
                    status: str = "success", duration_ms: Optional[int] = None,
                    error_message: Optional[str] = None, initiated_by: str = "cli",
                    run_id: Optional[str] = None) -> Tuple[bool, str]:
        """
        Commit a new version of the model.
        
        If tag is provided and already exists for this project, it overrides the existing entry.
        
        Returns:
            (committed: bool, snapshot_id: str)
        """
        # 1. Override Check: If tag exists, update the most recent one with that tag
        if tag:
            existing = self.get_snapshot_by_tag(project_id, tag)
            if existing:
                logger.info(f"Overriding existing snapshot {existing.snapshot_id} with tag: {tag}")
                snapshot_id = existing.snapshot_id
                timestamp = datetime.utcnow()
                
                conn = self._get_connection()
                conn.execute("""
                    UPDATE snapshots SET 
                        timestamp = ?,
                        sml_blob = ?,
                        status = ?,
                        duration_ms = ?,
                        error_message = ?,
                        initiated_by = ?,
                        run_id = ?
                    WHERE snapshot_id = ?
                """, [timestamp, json.dumps(sml_json), status, duration_ms, error_message, initiated_by, run_id, snapshot_id])
                return True, snapshot_id

        head = self.get_head(project_id)
        
        # Check for changes if HEAD exists
        changes = []
        if head:
            changes = self._compute_diff(head.sml_blob, sml_json)
            if not changes and not tag:
                logger.info("No changes detected. Skipping commit.")
                return False, head.snapshot_id
        else:
             logger.info("No previous history. Initial commit.")
        
        # Create new snapshot
        snapshot_id = str(uuid.uuid4())
        timestamp = datetime.utcnow()
        
        conn = self._get_connection()
        try:
            conn.begin()
            
            # Insert Snapshot
            conn.execute("""
                INSERT INTO snapshots (snapshot_id, project_id, timestamp, version_tag, sml_blob,
                                     status, duration_ms, error_message, initiated_by, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [snapshot_id, project_id, timestamp, tag, json.dumps(sml_json),
                  status, duration_ms, error_message, initiated_by, run_id])
            
            # Insert Changes
            if changes:
                for change in changes:
                    change_id = str(uuid.uuid4())
                    conn.execute("""
                        INSERT INTO changes (change_id, snapshot_id, object_type, object_name, diff_type, old_value, new_value)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, [
                        change_id,
                        snapshot_id,
                        change.object_type,
                        change.object_name,
                        change.diff_type,
                        json.dumps(change.old_value) if change.old_value else None,
                        json.dumps(change.new_value) if change.new_value else None
                    ])
            
            conn.commit()
            logger.info(f"Committed snapshot {snapshot_id} with {len(changes)} changes")
            return True, snapshot_id
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Commit failed: {e}")
            raise e
            
    def _compute_diff(self, old_json: Dict[str, Any], new_json: Dict[str, Any]) -> List[ModelChange]:
        """
        Compute diff between two SML JSON objects using simple Python comparison for now.
        Can be upgraded to DuckDB SQL-based JSON diff for massive models.
        """
        changes = []
        
        # Fields to exclude from comparison (volatile/metadata fields that change between extractions)
        EXCLUDE_FIELDS = {
            'created_at', 'modified_at', 'confidence', 'row_count',
            'run_id', 'snapshot_id', 'timestamp', 'duration_ms'
        }
        
        def normalize_value(v):
            """Normalize a value for comparison - sort lists to ignore order."""
            if isinstance(v, list):
                # Sort list if all elements are comparable (strings, numbers)
                try:
                    return sorted(v, key=str)
                except TypeError:
                    return v
            return v
        
        def normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
            """Remove volatile fields and normalize lists for comparison."""
            return {k: normalize_value(v) for k, v in item.items() if k not in EXCLUDE_FIELDS}
        
        # Compare objects: Metrics, Dimensions, Datasets
        # Helper to index lists by unique_name
        def index_by_name(items):
            return {item["unique_name"]: item for item in items}
        
        section_map = {
            "metrics": "metric",
            "dimensions": "dimension",
            "datasets": "dataset",
            "relationships": "relationship"
        }
        
        for section, type_name in section_map.items():
            old_items = index_by_name(old_json.get(section, []))
            new_items = index_by_name(new_json.get(section, []))
            
            all_keys = set(old_items.keys()) | set(new_items.keys())
            
            for key in all_keys:
                if key not in old_items:
                    changes.append(ModelChange(
                        object_type=type_name,
                        object_name=key,
                        diff_type="ADDED",
                        new_value=new_items[key]
                    ))
                elif key not in new_items:
                    changes.append(ModelChange(
                        object_type=type_name,
                        object_name=key,
                        diff_type="DELETED",
                        old_value=old_items[key]
                    ))
                else:
                    # Normalize both items before comparing (exclude volatile fields)
                    old_normalized = normalize_item(old_items[key])
                    new_normalized = normalize_item(new_items[key])
                    
                    if json.dumps(old_normalized, sort_keys=True) != json.dumps(new_normalized, sort_keys=True):
                        changes.append(ModelChange(
                            object_type=type_name,
                            object_name=key,
                            diff_type="MODIFIED",
                            old_value=old_items[key],
                            new_value=new_items[key]
                        ))
                
        return changes
    
    def get_snapshot(self, snapshot_id: str) -> Optional[Snapshot]:
        """Get a specific snapshot by ID."""
        conn = self._get_connection()
        result = conn.execute("""
            SELECT snapshot_id, project_id, timestamp, version_tag, sml_blob,
                   status, duration_ms, error_message, initiated_by, run_id
            FROM snapshots
            WHERE snapshot_id = ?
        """, [snapshot_id]).fetchone()
        if not result:
            return None
        return Snapshot(
            snapshot_id=result[0],
            project_id=result[1],
            timestamp=str(result[2]),
            version_tag=result[3],
            sml_blob=json.loads(result[4]),
            status=result[5],
            duration_ms=result[6],
            error_message=result[7],
            initiated_by=result[8],
            run_id=result[9]
        )
    
    def list_snapshots(self, project_id: str, limit: int = 10) -> List[Snapshot]:
        """List snapshots for a project, newest first."""
        conn = self._get_connection()
        results = conn.execute("""
            SELECT snapshot_id, project_id, timestamp, version_tag, sml_blob,
                   status, duration_ms, error_message, initiated_by, run_id
            FROM snapshots
            WHERE project_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, [project_id, limit]).fetchall()
        return [
            Snapshot(
                snapshot_id=r[0],
                project_id=r[1],
                timestamp=str(r[2]),
                version_tag=r[3],
                sml_blob=json.loads(r[4]),
                status=r[5],
                duration_ms=r[6],
                error_message=r[7],
                initiated_by=r[8],
                run_id=r[9]
            )
            for r in results
        ]
    
    def rollback(self, project_id: str, target_snapshot_id: str, tag: str = None) -> Tuple[bool, str, List[ModelChange]]:
        """
        Rollback to a previous snapshot.
        
        This creates a NEW commit with the state from target_snapshot_id.
        The history is preserved (non-destructive rollback).
        
        Returns:
            (success: bool, new_snapshot_id: str, changes: List[ModelChange])
        """
        # Get current HEAD
        head = self.get_head(project_id)
        if not head:
            logger.error("No HEAD found for project")
            return False, "", []
        
        # Get target snapshot
        target = self.get_snapshot(target_snapshot_id)
        if not target:
            logger.error(f"Target snapshot {target_snapshot_id} not found")
            return False, "", []
        
        if target.project_id != project_id:
            logger.error(f"Snapshot {target_snapshot_id} does not belong to project {project_id}")
            return False, "", []
        
        # Compute changes (what we're reverting)
        changes = self._compute_diff(head.sml_blob, target.sml_blob)
        
        # Commit the target state as a new snapshot
        rollback_tag = tag or f"rollback_to_{target.version_tag or target_snapshot_id[:8]}"
        committed, new_snapshot_id = self.commit_model(project_id, target.sml_blob, rollback_tag)
        
        if committed:
            logger.info(f"Rolled back to snapshot {target_snapshot_id[:12]}... (new snapshot: {new_snapshot_id[:12]}...)")
        
        return committed, new_snapshot_id, changes
    
    def compare_versions(self, project_id: str, old_snapshot_id: str, new_snapshot_id: str) -> List[Dict[str, Any]]:
        """
        Compare two versions and return tabular diff.
        
        Returns a list of dicts with format:
        [
            {"object": "metric.Revenue", "previous_version": "SUM([Amount])", "new_version": "SUM([Revenue])"},
            {"object": "dataset.Customer", "previous_version": "—", "new_version": "{...}"},
            ...
        ]
        """
        old_snap = self.get_snapshot(old_snapshot_id)
        new_snap = self.get_snapshot(new_snapshot_id)
        
        if not old_snap or not new_snap:
            return []
        
        if old_snap.project_id != project_id or new_snap.project_id != project_id:
            return []
        
        changes = self._compute_diff(old_snap.sml_blob, new_snap.sml_blob)
        
        tabular_diff = []
        for change in changes:
            row = {
                "object": f"{change.object_type}.{change.object_name}",
                "previous_version": json.dumps(change.old_value, indent=2) if change.old_value else "—",
                "new_version": json.dumps(change.new_value, indent=2) if change.new_value else "—",
            }
            tabular_diff.append(row)
        
        return tabular_diff
    
    def compare_versions_markdown(self, project_id: str, old_snapshot_id: str, new_snapshot_id: str) -> str:
        """
        Compare two versions and return a markdown table.
        
        Format:
        | Object | Previous Version | New Version |
        |--------|------------------|-------------|
        | metric.Revenue | SUM([Amount]) | SUM([Revenue]) |
        """
        diff = self.compare_versions(project_id, old_snapshot_id, new_snapshot_id)
        
        if not diff:
            return "No changes detected."
        
        lines = ["| Object | Previous Version | New Version |", "|--------|------------------|-------------|"]
        
        for row in diff:
            # Escape newlines and pipes for markdown
            prev = row["previous_version"].replace("\n", " ").replace("|", "\\|")[:100]
            new = row["new_version"].replace("\n", " ").replace("|", "\\|")[:100]
            lines.append(f"| {row['object']} | {prev} | {new} |")
        
        return "\n".join(lines)
    
    def get_snapshot_by_tag(self, project_id: str, tag: str) -> Optional[Snapshot]:
        """Get a snapshot by its version tag."""
        conn = self._get_connection()
        result = conn.execute("""
            SELECT snapshot_id, project_id, timestamp, version_tag, sml_blob,
                   status, duration_ms, error_message, initiated_by, run_id
            FROM snapshots
            WHERE project_id = ? AND version_tag = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, [project_id, tag]).fetchone()
        if not result:
            return None
        return Snapshot(
            snapshot_id=result[0],
            project_id=result[1],
            timestamp=str(result[2]),
            version_tag=result[3],
            sml_blob=json.loads(result[4]),
            status=result[5],
            duration_ms=result[6],
            error_message=result[7],
            initiated_by=result[8],
            run_id=result[9]
        )
    
    def persist_source_artifact(self, run_id: str, source_format: Any) -> Optional[str]:
        """
        Persist a Source Format artifact (Step 7).
        
        Args:
            run_id: The run ID this artifact belongs to
            source_format: SourceFormat object to persist
            
        Returns:
            artifact_id if successful, None otherwise
        """
        if source_format is None:
            return None
        
        artifact_id = str(uuid.uuid4())
        timestamp = datetime.utcnow()
        
        conn = self._get_connection()
        try:
            # Serialize the source format
            if hasattr(source_format, 'model_dump'):
                content = source_format.model_dump(mode='json')
            else:
                content = dict(source_format) if hasattr(source_format, '__iter__') else {}
            
            source_type = getattr(source_format, 'source_type', 'unknown')
            
            conn.execute("""
                INSERT INTO source_artifacts (artifact_id, run_id, source_type, content_json, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, [artifact_id, run_id, source_type, json.dumps(content), timestamp])
            
            logger.info(f"Persisted source artifact {artifact_id[:12]}... for run {run_id[:12]}...")
            return artifact_id
            
        except Exception as e:
            logger.error(f"Failed to persist source artifact: {e}")
            return None
    
    def get_source_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        """Get a source artifact by ID."""
        conn = self._get_connection()
        result = conn.execute("""
            SELECT artifact_id, run_id, source_type, content_json, created_at
            FROM source_artifacts
            WHERE artifact_id = ?
        """, [artifact_id]).fetchone()
        if not result:
            return None
        return {
            "artifact_id": result[0],
            "run_id": result[1],
            "source_type": result[2],
            "content": json.loads(result[3]),
            "created_at": str(result[4]),
        }
    
    def record_run_start(self, run_id: str, project_id: str, source_type: str, target_type: Optional[str] = None) -> None:
        """Record the start of an execution run (Step 2)."""
        timestamp = datetime.utcnow()
        conn = self._get_connection()
        conn.execute("""
            INSERT INTO runs (run_id, project_id, started_at, status, source_type, target_type)
            VALUES (?, ?, ?, 'running', ?, ?)
        """, [run_id, project_id, timestamp, source_type, target_type])
    
    def record_run_complete(
        self,
        run_id: str,
        status: str,
        final_step: int,
        duration_ms: int,
        error_message: Optional[str] = None
    ) -> None:
        """Record the completion of an execution run (Step 10)."""
        timestamp = datetime.utcnow()
        conn = self._get_connection()
        conn.execute("""
            UPDATE runs
            SET completed_at = ?,
                status = ?,
                final_step = ?,
                duration_ms = ?,
                error_message = ?
            WHERE run_id = ?
        """, [timestamp, status, final_step, duration_ms, error_message, run_id])
