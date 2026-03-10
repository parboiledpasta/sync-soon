"""
Command Logger.

Persistent logging of all SemaBridge CLI commands with:
- Command type tracking (sync, rollback, diff, validate, etc.)
- Status tracking (success, failed, in_progress)
- Duration and timing metrics
- Adapter-specific context
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from semabridge.utils.logger import get_logger
from semabridge.utils.enterprise_logger import get_enterprise_logger

logger = get_logger(__name__)


class CommandType(str, Enum):
    """Types of CLI commands."""
    DEPLOY = "deploy"
    COMPARE = "compare"
    ROLLBACK = "rollback"
    VALIDATE = "validate"
    EXTRACT = "extract"  # Internal step
    BUILD = "build"      # Internal step
    EMIT = "emit"        # Internal step
    PUBLISH = "publish"  # Internal step
    HISTORY = "history"
    LIST_PROJECTS = "list_projects"
    STATUS_CHECK = "status_check"
    CONFIG = "config"
    LOGS = "logs"
    
    # Deprecated (for historical logs)
    SYNC = "sync"
    REVERSE_SYNC = "reverse_sync"
    DIFF = "diff"
    RUN = "run"


class ActionType(str, Enum):
    """High-level action categories."""
    DEPLOYMENT = "deployment"
    ROLLBACK_OPERATION = "rollback_operation"
    COMPARISON = "comparison"
    VALIDATION = "validation"
    QUERY = "query"
    CONFIGURATION = "configuration"
    
    # Deprecated
    SYNC_OPERATION = "sync_operation"


class CommandStatus(str, Enum):
    """Command execution status."""
    STARTED = "started"
    IN_PROGRESS = "in_progress"
    PREVIEW = "preview"  # For preview phase of rollback
    CONFIRMED = "confirmed"  # User confirmed action
    ABORTED = "aborted"  # User aborted action
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class CommandLogEntry:
    """A single command log entry."""
    log_id: str
    command: CommandType
    action_type: ActionType
    status: CommandStatus
    started_at: str
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    adapter: Optional[str] = None
    project_id: Optional[str] = None
    initiated_by: str = "cli"
    error_message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def create(
        cls,
        command: CommandType,
        action_type: ActionType,
        adapter: Optional[str] = None,
        project_id: Optional[str] = None,
        initiated_by: str = "cli",
        details: Optional[Dict[str, Any]] = None,
    ) -> "CommandLogEntry":
        """Create a new command log entry."""
        return cls(
            log_id=str(uuid.uuid4()),
            command=command,
            action_type=action_type,
            status=CommandStatus.STARTED,
            started_at=datetime.now(timezone.utc).isoformat(),
            adapter=adapter,
            project_id=project_id,
            initiated_by=initiated_by,
            details=details or {},
        )
    
    def mark_success(self, duration_ms: int, details: Optional[Dict[str, Any]] = None):
        """Mark command as successful."""
        self.status = CommandStatus.SUCCESS
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.duration_ms = duration_ms
        if details:
            self.details.update(details)
    
    def mark_failed(self, error: str, duration_ms: int = 0):
        """Mark command as failed."""
        self.status = CommandStatus.FAILED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.duration_ms = duration_ms
        self.error_message = error


class CommandLogger:
    """
    Persistent command logger using DuckDB.
    
    Tracks all CLI command executions for audit and debugging.
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """Initialize the command logger."""
        self.db_path = db_path
        self._init_schema()
    
    def _get_connection(self):
        """Get DuckDB connection."""
        import duckdb
        from pathlib import Path
        
        if self.db_path is None:
            try:
                from semabridge.core.settings import get_settings
                settings = get_settings()
                self.db_path = str(Path(settings.core.repository_path).expanduser().resolve())
            except Exception:
                self.db_path = str(Path.cwd() / "semabridge.db")
        
        return duckdb.connect(self.db_path)
    
    def _init_schema(self):
        """Initialize command log table."""
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS command_log (
                    log_id VARCHAR PRIMARY KEY,
                    command VARCHAR NOT NULL,
                    action_type VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP,
                    duration_ms INTEGER,
                    adapter VARCHAR,
                    project_id VARCHAR,
                    initiated_by VARCHAR DEFAULT 'cli',
                    error_message VARCHAR,
                    details JSON
                )
            """)
            
            # Create index on started_at for efficient querying
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_command_log_started 
                ON command_log(started_at DESC)
            """)
        finally:
            conn.close()
    
    def log_start(
        self,
        command: CommandType,
        action_type: ActionType,
        adapter: Optional[str] = None,
        project_id: Optional[str] = None,
        initiated_by: str = "cli",
        details: Optional[Dict[str, Any]] = None,
    ) -> CommandLogEntry:
        """Log the start of a command."""
        import json
        
        entry = CommandLogEntry.create(
            command=command,
            action_type=action_type,
            adapter=adapter,
            project_id=project_id,
            initiated_by=initiated_by,
            details=details,
        )
        
        conn = self._get_connection()
        try:
            conn.execute("""
                INSERT INTO command_log 
                (log_id, command, action_type, status, started_at, adapter, 
                 project_id, initiated_by, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                entry.log_id,
                entry.command.value,
                entry.action_type.value,
                entry.status.value,
                entry.started_at,
                entry.adapter,
                entry.project_id,
                entry.initiated_by,
                json.dumps(entry.details),
            ])
            
            logger.debug(f"Command started: {command.value} (id={entry.log_id[:8]})")
            
            # Write to Enterprise Logger (text logs)
            try:
                ent_logger = get_enterprise_logger()
                ent_logger.log_operation_start(
                    operation_id=entry.log_id,
                    command=entry.command.value,
                    adapter=entry.adapter,
                    project_id=entry.project_id or "n/a",
                )
            except Exception:
                pass  # Don't fail main operation if enterprise logging fails
        finally:
            conn.close()
        
        return entry
    
    def log_success(
        self,
        entry: CommandLogEntry,
        duration_ms: int,
        details: Optional[Dict[str, Any]] = None,
    ):
        """Log successful command completion."""
        import json
        
        entry.mark_success(duration_ms, details)
        
        conn = self._get_connection()
        try:
            conn.execute("""
                UPDATE command_log 
                SET status = ?, completed_at = ?, duration_ms = ?, details = ?
                WHERE log_id = ?
            """, [
                entry.status.value,
                entry.completed_at,
                entry.duration_ms,
                json.dumps(entry.details),
                entry.log_id,
            ])
            
            logger.info(
                f"Command completed: {entry.command.value} "
                f"(id={entry.log_id[:8]}, duration={duration_ms}ms)"
            )
            
            # Write to Enterprise Logger (text logs)
            try:
                ent_logger = get_enterprise_logger()
                ent_logger.log_operation_success(
                    operation_id=entry.log_id,
                    duration_ms=duration_ms,
                    new_version=entry.details.get("snapshot_id"),
                )
            except Exception:
                pass  # Don't fail main operation if enterprise logging fails
        finally:
            conn.close()
    
    def log_failure(
        self,
        entry: CommandLogEntry,
        error: str,
        duration_ms: int = 0,
    ):
        """Log command failure."""
        entry.mark_failed(error, duration_ms)
        
        conn = self._get_connection()
        try:
            conn.execute("""
                UPDATE command_log 
                SET status = ?, completed_at = ?, duration_ms = ?, error_message = ?
                WHERE log_id = ?
            """, [
                entry.status.value,
                entry.completed_at,
                entry.duration_ms,
                entry.error_message,
                entry.log_id,
            ])
            
            logger.error(
                f"Command failed: {entry.command.value} "
                f"(id={entry.log_id[:8]}, error={error[:100]})"
            )
            
            # Write to Enterprise Logger (text logs)
            try:
                ent_logger = get_enterprise_logger()
                ent_logger.log_operation_failed(
                    operation_id=entry.log_id,
                    error=error,
                    duration_ms=duration_ms,
                )
            except Exception:
                pass  # Don't fail main operation if enterprise logging fails
        finally:
            conn.close()
    
    def log_phase(
        self,
        entry: CommandLogEntry,
        phase: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        """Log a phase transition (e.g., preview, confirmed)."""
        import json
        
        entry.status = CommandStatus(phase) if phase in [s.value for s in CommandStatus] else CommandStatus.IN_PROGRESS
        if details:
            entry.details.update(details)
        entry.details["phase"] = phase
        
        conn = self._get_connection()
        try:
            conn.execute("""
                UPDATE command_log 
                SET status = ?, details = ?
                WHERE log_id = ?
            """, [
                entry.status.value,
                json.dumps(entry.details),
                entry.log_id,
            ])
            
            logger.info(
                f"Command phase: {entry.command.value} -> {phase} "
                f"(id={entry.log_id[:8]})"
            )
        finally:
            conn.close()
    
    def log_aborted(
        self,
        entry: CommandLogEntry,
        reason: str = "User cancelled",
        duration_ms: int = 0,
    ):
        """Log command aborted by user."""
        import json
        
        entry.status = CommandStatus.ABORTED
        entry.completed_at = datetime.now(timezone.utc).isoformat()
        entry.duration_ms = duration_ms
        entry.details["abort_reason"] = reason
        
        conn = self._get_connection()
        try:
            conn.execute("""
                UPDATE command_log 
                SET status = ?, completed_at = ?, duration_ms = ?, details = ?
                WHERE log_id = ?
            """, [
                entry.status.value,
                entry.completed_at,
                entry.duration_ms,
                json.dumps(entry.details),
                entry.log_id,
            ])
            
            logger.warning(
                f"Command aborted: {entry.command.value} "
                f"(id={entry.log_id[:8]}, reason={reason})"
            )
        finally:
            conn.close()
    
    def get_recent_logs(
        self,
        limit: int = 20,
        command_filter: Optional[CommandType] = None,
        status_filter: Optional[CommandStatus] = None,
    ) -> List[CommandLogEntry]:
        """Get recent command logs."""
        import json
        
        conn = self._get_connection()
        try:
            query = "SELECT * FROM command_log"
            conditions = []
            params = []
            
            if command_filter:
                conditions.append("command = ?")
                params.append(command_filter.value)
            
            if status_filter:
                conditions.append("status = ?")
                params.append(status_filter.value)
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            
            query += " ORDER BY started_at DESC LIMIT ?"
            params.append(limit)
            
            results = conn.execute(query, params).fetchall()
            
            entries = []
            for row in results:
                details = row[11] if row[11] else {}
                if isinstance(details, str):
                    details = json.loads(details)
                
                entries.append(CommandLogEntry(
                    log_id=row[0],
                    command=CommandType(row[1]),
                    action_type=ActionType(row[2]),
                    status=CommandStatus(row[3]),
                    started_at=str(row[4]),
                    completed_at=str(row[5]) if row[5] else None,
                    duration_ms=row[6],
                    adapter=row[7],
                    project_id=row[8],
                    initiated_by=row[9],
                    error_message=row[10],
                    details=details,
                ))
            
            return entries
        finally:
            conn.close()
    
    def get_command_stats(self, days: int = 30) -> Dict[str, Any]:
        """Get command statistics for the last N days."""
        conn = self._get_connection()
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            stats = conn.execute("""
                SELECT 
                    command,
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_count,
                    AVG(duration_ms) as avg_duration_ms
                FROM command_log
                WHERE started_at >= ?
                GROUP BY command
                ORDER BY total DESC
            """, [cutoff_date]).fetchall()
            
            return {
                "period_days": days,
                "commands": [
                    {
                        "command": row[0],
                        "total": row[1],
                        "success": row[2],
                        "failed": row[3],
                        "success_rate": round(row[2] / row[1] * 100, 1) if row[1] > 0 else 0,
                        "avg_duration_ms": int(row[4]) if row[4] else 0,
                    }
                    for row in stats
                ]
            }
        finally:
            conn.close()


# Singleton instance
_command_logger: Optional[CommandLogger] = None


def get_command_logger() -> CommandLogger:
    """Get the singleton command logger instance."""
    global _command_logger
    if _command_logger is None:
        _command_logger = CommandLogger()
    return _command_logger
