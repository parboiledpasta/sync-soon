"""
Unit tests for the Semantic API components.
"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestSemanticVersionManager:
    """Tests for SemanticVersionManager."""
    
    @pytest.fixture
    def temp_metadata_dir(self):
        """Create temporary metadata directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @pytest.fixture
    def version_manager(self, temp_metadata_dir):
        """Create version manager with temp directory."""
        from semabridge.repository.semantic_version_manager import SemanticVersionManager
        return SemanticVersionManager(metadata_dir=temp_metadata_dir)
    
    def test_generate_version_id_format(self, version_manager):
        """Test version ID format: v{YYYYMMDD_HHMMSS}_{adapter}_{sequence}."""
        version_id = version_manager._generate_version_id("snowflake")
        
        assert version_id.startswith("v")
        assert "snowflake" in version_id
        assert version_id.endswith("_001")
        
        # Second call should increment sequence
        version_id_2 = version_manager._generate_version_id("snowflake")
        assert version_id_2.endswith("_002")
    
    def test_compute_schema_hash(self, version_manager):
        """Test schema hash computation is deterministic."""
        state1 = {"metrics": [{"name": "revenue"}], "datasets": []}
        state2 = {"metrics": [{"name": "revenue"}], "datasets": []}
        state3 = {"metrics": [{"name": "profit"}], "datasets": []}
        
        hash1 = version_manager.compute_schema_hash(state1)
        hash2 = version_manager.compute_schema_hash(state2)
        hash3 = version_manager.compute_schema_hash(state3)
        
        assert hash1 == hash2  # Same content = same hash
        assert hash1 != hash3  # Different content = different hash
        assert len(hash1) == 64  # SHA256 hex
    
    def test_create_version_record(self, version_manager):
        """Test creating a version record."""
        state = {"metrics": [], "datasets": []}
        
        record = version_manager.create_version_record(
            adapter="snowflake",
            semantic_state=state,
            change_type="deploy",
            change_summary="Initial deployment",
        )
        
        assert record.version_id.startswith("v")
        assert record.adapter == "snowflake"
        assert record.change_type == "deploy"
        assert record.change_summary == "Initial deployment"
        assert record.status == "active"
        assert record.parent_version_id is None
    
    def test_parent_version_tracking(self, version_manager):
        """Test parent-child version relationship."""
        state1 = {"metrics": [], "datasets": []}
        state2 = {"metrics": [{"name": "revenue"}], "datasets": []}
        
        record1 = version_manager.create_version_record(
            adapter="snowflake",
            semantic_state=state1,
            change_summary="v1",
        )
        
        record2 = version_manager.create_version_record(
            adapter="snowflake",
            semantic_state=state2,
            change_summary="v2",
        )
        
        assert record2.parent_version_id == record1.version_id
        
        # First version should be superseded
        v1 = version_manager.get_version(record1.version_id)
        assert v1.status == "superseded"
    
    def test_get_current_version(self, version_manager):
        """Test getting current (latest active) version."""
        assert version_manager.get_current_version("snowflake") is None
        
        state = {"metrics": [], "datasets": []}
        record = version_manager.create_version_record(
            adapter="snowflake",
            semantic_state=state,
        )
        
        current = version_manager.get_current_version("snowflake")
        assert current is not None
        assert current.version_id == record.version_id
    
    def test_get_version_history(self, version_manager):
        """Test getting version history."""
        states = [{"v": i} for i in range(5)]
        
        for i, state in enumerate(states):
            version_manager.create_version_record(
                adapter="snowflake",
                semantic_state=state,
                change_summary=f"v{i}",
            )
        
        # Include superseded to get full history (only latest is active by default)
        history = version_manager.get_version_history("snowflake", limit=3, include_superseded=True)
        
        assert len(history) == 3
        # Newest first
        assert history[0].change_summary == "v4"
        assert history[1].change_summary == "v3"
    
    def test_rollback_metadata(self, version_manager):
        """Test rollback metadata linking."""
        state = {"metrics": [], "datasets": []}
        
        v1 = version_manager.create_version_record(
            adapter="snowflake",
            semantic_state=state,
            change_summary="v1",
        )
        
        v2 = version_manager.create_version_record(
            adapter="snowflake",
            semantic_state={"changed": True},
            change_summary="v2",
        )
        
        v3_rollback = version_manager.create_version_record(
            adapter="snowflake",
            semantic_state=state,
            change_type="rollback",
            change_summary="Rollback to v1",
        )
        
        version_manager.link_rollback_operation(
            from_version_id=v2.version_id,
            to_version_id=v1.version_id,
            rollback_version_id=v3_rollback.version_id,
            reason="Bug fix",
        )
        
        v3 = version_manager.get_version(v3_rollback.version_id)
        assert v3.rollback_metadata is not None
        assert v3.rollback_metadata["from_version"] == v2.version_id
        assert v3.rollback_metadata["to_version"] == v1.version_id
        assert v3.rollback_metadata["reason"] == "Bug fix"
    
    def test_version_exists(self, version_manager):
        """Test version existence check."""
        assert not version_manager.version_exists("nonexistent")
        
        state = {"metrics": [], "datasets": []}
        record = version_manager.create_version_record(
            adapter="snowflake",
            semantic_state=state,
        )
        
        assert version_manager.version_exists(record.version_id)
    
    def test_registry_persistence(self, temp_metadata_dir):
        """Test registry is saved and loaded correctly."""
        from semabridge.repository.semantic_version_manager import SemanticVersionManager
        
        # Create manager and add version
        manager1 = SemanticVersionManager(metadata_dir=temp_metadata_dir)
        state = {"metrics": [], "datasets": []}
        record = manager1.create_version_record(
            adapter="snowflake",
            semantic_state=state,
        )
        
        # Create new manager and verify it loads the data
        manager2 = SemanticVersionManager(metadata_dir=temp_metadata_dir)
        loaded = manager2.get_version(record.version_id)
        
        assert loaded is not None
        assert loaded.version_id == record.version_id


class TestSemanticSnapshotManager:
    """Tests for SemanticSnapshotManager."""
    
    @pytest.fixture
    def temp_metadata_dir(self):
        """Create temporary metadata directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @pytest.fixture
    def snapshot_manager(self, temp_metadata_dir):
        """Create snapshot manager with temp directory."""
        from semabridge.repository.semantic_snapshot_manager import SemanticSnapshotManager
        return SemanticSnapshotManager(metadata_dir=temp_metadata_dir)
    
    def test_create_snapshot(self, snapshot_manager):
        """Test creating a snapshot."""
        state = {
            "metrics": [{"unique_name": "revenue", "label": "Revenue"}],
            "dimensions": [{"unique_name": "date", "label": "Date"}],
            "relationships": [],
        }
        
        snapshot = snapshot_manager.create_snapshot(
            adapter="snowflake",
            semantic_state=state,
            change_description="Test snapshot",
        )
        
        assert snapshot.snapshot_id.startswith("v")
        assert snapshot.adapter == "snowflake"
        assert snapshot.measure_count == 1
        assert snapshot.dimension_count == 1
        assert snapshot.change_description == "Test snapshot"
        assert len(snapshot.schema_hash) == 64
    
    def test_get_snapshot(self, snapshot_manager):
        """Test retrieving a snapshot."""
        state = {"metrics": [], "datasets": []}
        
        created = snapshot_manager.create_snapshot(
            adapter="snowflake",
            semantic_state=state,
        )
        
        retrieved = snapshot_manager.get_snapshot(created.snapshot_id)
        
        assert retrieved is not None
        assert retrieved.snapshot_id == created.snapshot_id
        assert retrieved.schema_hash == created.schema_hash
    
    def test_list_snapshots(self, snapshot_manager):
        """Test listing snapshots."""
        for i in range(5):
            snapshot_manager.create_snapshot(
                adapter="snowflake",
                semantic_state={"v": i},
                change_description=f"v{i}",
            )
        
        snapshots = snapshot_manager.list_snapshots("snowflake", limit=3)
        
        assert len(snapshots) == 3
        # Newest first
        assert snapshots[0].change_description == "v4"
    
    def test_validate_snapshot_integrity(self, snapshot_manager):
        """Test snapshot integrity validation."""
        state = {"metrics": [], "datasets": []}
        
        snapshot = snapshot_manager.create_snapshot(
            adapter="snowflake",
            semantic_state=state,
        )
        
        # Valid integrity
        assert snapshot_manager.validate_snapshot_integrity(snapshot.snapshot_id)
    
    def test_parent_snapshot_tracking(self, snapshot_manager):
        """Test parent-child snapshot relationship."""
        s1 = snapshot_manager.create_snapshot(
            adapter="snowflake",
            semantic_state={"v": 1},
        )
        
        s2 = snapshot_manager.create_snapshot(
            adapter="snowflake",
            semantic_state={"v": 2},
        )
        
        assert s2.parent_snapshot_id == s1.snapshot_id
    
    def test_get_latest_snapshot(self, snapshot_manager):
        """Test getting the most recent snapshot."""
        assert snapshot_manager.get_latest_snapshot("snowflake") is None
        
        for i in range(3):
            snapshot_manager.create_snapshot(
                adapter="snowflake",
                semantic_state={"v": i},
            )
        
        latest = snapshot_manager.get_latest_snapshot("snowflake")
        assert latest is not None


class TestSemanticDiffEngine:
    """Tests for SemanticDiffEngine."""
    
    @pytest.fixture
    def temp_metadata_dir(self):
        """Create temporary metadata directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @pytest.fixture
    def diff_engine(self, temp_metadata_dir):
        """Create diff engine with temp directory."""
        from semabridge.repository.semantic_snapshot_manager import SemanticSnapshotManager
        from semabridge.repository.semantic_diff_engine import SemanticDiffEngine
        
        snapshot_manager = SemanticSnapshotManager(metadata_dir=temp_metadata_dir)
        return SemanticDiffEngine(snapshot_manager), snapshot_manager
    
    def test_compare_states_added(self, diff_engine):
        """Test detecting added entities."""
        engine, _ = diff_engine
        
        state1 = {"metrics": [], "dimensions": [], "relationships": [], "datasets": []}
        state2 = {
            "metrics": [{"unique_name": "revenue", "label": "Revenue"}],
            "dimensions": [],
            "relationships": [],
            "datasets": [],
        }
        
        diff = engine.compare_states(state1, state2)
        
        assert diff.summary.measures_added == 1
        assert diff.summary.measures_removed == 0
        assert len(diff.changes) == 1
        assert diff.changes[0].change_type.value == "added"
    
    def test_compare_states_removed(self, diff_engine):
        """Test detecting removed entities."""
        engine, _ = diff_engine
        
        state1 = {
            "metrics": [{"unique_name": "revenue", "label": "Revenue"}],
            "dimensions": [],
            "relationships": [],
            "datasets": [],
        }
        state2 = {"metrics": [], "dimensions": [], "relationships": [], "datasets": []}
        
        diff = engine.compare_states(state1, state2)
        
        assert diff.summary.measures_removed == 1
        assert diff.summary.has_breaking_changes
        assert len(diff.breaking_changes) == 1
    
    def test_compare_states_modified(self, diff_engine):
        """Test detecting modified entities."""
        engine, _ = diff_engine
        
        state1 = {
            "metrics": [{"unique_name": "revenue", "label": "Revenue", "expression": "SUM(sales)"}],
            "dimensions": [],
            "relationships": [],
            "datasets": [],
        }
        state2 = {
            "metrics": [{"unique_name": "revenue", "label": "Revenue", "expression": "SUM(sales * 1.1)"}],
            "dimensions": [],
            "relationships": [],
            "datasets": [],
        }
        
        diff = engine.compare_states(state1, state2)
        
        assert diff.summary.measures_modified == 1
        assert diff.changes[0].change_type.value == "modified"
    
    def test_compare_snapshots(self, diff_engine):
        """Test comparing actual snapshots."""
        engine, snapshot_manager = diff_engine
        
        s1 = snapshot_manager.create_snapshot(
            adapter="snowflake",
            semantic_state={"metrics": [], "dimensions": [], "relationships": [], "datasets": []},
        )
        
        s2 = snapshot_manager.create_snapshot(
            adapter="snowflake",
            semantic_state={
                "metrics": [{"unique_name": "revenue", "label": "Revenue"}],
                "dimensions": [],
                "relationships": [],
                "datasets": [],
            },
        )
        
        diff = engine.compare_snapshots(s1.snapshot_id, s2.snapshot_id)
        
        assert diff.from_snapshot_id == s1.snapshot_id
        assert diff.to_snapshot_id == s2.snapshot_id
        assert diff.summary.measures_added == 1
    
    def test_export_json(self, diff_engine):
        """Test exporting diff as JSON."""
        engine, _ = diff_engine
        
        state1 = {"metrics": [], "dimensions": [], "relationships": [], "datasets": []}
        state2 = {
            "metrics": [{"unique_name": "revenue", "label": "Revenue"}],
            "dimensions": [],
            "relationships": [],
            "datasets": [],
        }
        
        diff = engine.compare_states(state1, state2)
        json_str = engine.export_diff_as_report(diff, "json")
        
        # Should be valid JSON
        data = json.loads(json_str)
        assert "diff_summary" in data
        assert "changes" in data
    
    def test_export_cli(self, diff_engine):
        """Test exporting diff as CLI text."""
        engine, _ = diff_engine
        
        state1 = {"metrics": [], "dimensions": [], "relationships": [], "datasets": []}
        state2 = {
            "metrics": [{"unique_name": "revenue", "label": "Revenue"}],
            "dimensions": [],
            "relationships": [],
            "datasets": [],
        }
        
        diff = engine.compare_states(state1, state2)
        cli_str = engine.export_diff_as_report(diff, "cli")
        
        assert "SUMMARY" in cli_str
        assert "MEASURES" in cli_str
    
    def test_breaking_changes(self, diff_engine):
        """Test breaking change detection."""
        engine, _ = diff_engine
        
        state1 = {
            "metrics": [{"unique_name": "revenue", "label": "Revenue"}],
            "dimensions": [{"unique_name": "date", "label": "Date"}],
            "relationships": [{"unique_name": "rel1", "label": "Rel1"}],
            "datasets": [],
        }
        state2 = {"metrics": [], "dimensions": [], "relationships": [], "datasets": []}
        
        diff = engine.compare_states(state1, state2)
        
        assert diff.summary.has_breaking_changes
        assert len(diff.breaking_changes) == 3  # measure, dimension, relationship
        
        highlights = engine.highlight_breaking_changes(diff)
        assert len(highlights) == 3


class TestEnterpriseLogger:
    """Tests for EnterpriseLogger."""
    
    @pytest.fixture
    def temp_logs_dir(self):
        """Create temporary logs directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @pytest.fixture
    def logger(self, temp_logs_dir):
        """Create enterprise logger with temp directory."""
        from semabridge.utils.enterprise_logger import EnterpriseLogger
        return EnterpriseLogger(logs_dir=temp_logs_dir)
    
    def test_log_operation_start(self, logger):
        """Test logging operation start."""
        logger.log_operation_start(
            operation_id="op_001",
            command="reverse-sync",
            adapter="snowflake",
        )
        
        # Verify log file exists
        log_files = logger.get_log_files()
        assert len(log_files["operations"]) > 0
    
    def test_log_operation_success(self, logger):
        """Test logging operation success."""
        logger.log_operation_success(
            operation_id="op_001",
            duration_ms=1500,
            new_version="v20260119_001",
        )
        
        lines = logger.read_log("operations", lines=10)
        assert len(lines) > 0
        assert "OPERATION_SUCCESS" in "".join(lines)
    
    def test_log_operation_failed(self, logger):
        """Test logging operation failure."""
        logger.log_operation_failed(
            operation_id="op_001",
            error="Connection failed",
            duration_ms=500,
        )
        
        error_lines = logger.read_log("errors", lines=10)
        assert len(error_lines) > 0
    
    def test_log_rollback(self, logger):
        """Test logging rollback."""
        logger.log_rollback_initiated(
            operation_id="rb_001",
            adapter="snowflake",
            from_version="v2",
            to_version="v1",
            user="test_user",
            reason="Bug fix",
        )
        
        audit_lines = logger.read_log("audit", lines=10)
        assert len(audit_lines) > 0
        assert "ROLLBACK_INITIATED" in "".join(audit_lines)
    
    def test_log_search(self, logger):
        """Test log search functionality."""
        logger.log_operation_start(operation_id="op_001", command="sync")
        logger.log_operation_start(operation_id="op_002", command="rollback")
        logger.log_operation_start(operation_id="op_003", command="sync")
        
        # Search for "rollback"
        matches = logger.read_log("operations", lines=100, search_pattern="rollback")
        
        # At least one match
        rollback_matches = [l for l in matches if "rollback" in l.lower()]
        assert len(rollback_matches) >= 1
    
    def test_get_log_files(self, logger):
        """Test getting log file list."""
        logger.log_operation_start(operation_id="op_001", command="test")
        
        files = logger.get_log_files()
        
        assert "operations" in files
        assert "errors" in files
        assert "audit" in files
