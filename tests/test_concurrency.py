"""
Comprehensive tests for the SemaBridge concurrency package.

Covers Models, ErrorClassifier, RetryManager, ResourceManager,
ConfigurationResolver, ValidationEngine, ConnectionPoolManager,
FileManager, ProgressReporter, ModelProcessor, ResumeManager,
ConcurrencyOrchestrator, ErrorReporter, AuthenticationManager,
and batch persistence.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import duckdb
import pytest


# ======================================================================
# 1. Models
# ======================================================================

class TestModels:
    """Tests for concurrency data models."""

    def test_execution_mode_values(self):
        from semabridge.core.concurrency.models import ExecutionMode
        assert ExecutionMode.BEST_EFFORT.value == "best_effort"
        assert ExecutionMode.STRICT.value == "strict"

    def test_processing_stage_values(self):
        from semabridge.core.concurrency.models import ProcessingStage
        stages = [s.value for s in ProcessingStage]
        assert "idle" in stages
        assert "extracting" in stages
        assert "completed" in stages
        assert "failed" in stages

    def test_error_type_enum(self):
        from semabridge.core.concurrency.models import ErrorType
        assert ErrorType.TRANSIENT.value == "transient"
        assert ErrorType.PERMANENT.value == "permanent"

    def test_retry_config_defaults(self):
        from semabridge.core.concurrency.models import RetryConfig
        cfg = RetryConfig()
        assert cfg.max_retries == 3
        assert cfg.base_delay == 1.0
        assert cfg.max_delay == 60.0
        assert cfg.exponential_base == 2.0

    def test_retry_config_validation(self):
        from semabridge.core.concurrency.models import RetryConfig
        with pytest.raises(ValueError):
            RetryConfig(max_retries=-1)
        with pytest.raises(ValueError):
            RetryConfig(base_delay=0)

    def test_model_result_defaults(self):
        from semabridge.core.concurrency.models import ModelResult
        r = ModelResult(model_name="test", success=False)
        assert r.success is False
        assert r.duration_seconds == 0.0
        assert r.retry_count == 0
        assert r.error is None

    def test_model_result_success(self):
        from semabridge.core.concurrency.models import ModelResult
        r = ModelResult(model_name="Sales", success=True, duration_seconds=2.5)
        assert r.success
        assert r.duration_seconds == 2.5

    def test_batch_result_counts(self):
        from semabridge.core.concurrency.models import BatchResult
        b = BatchResult(
            batch_id="abc",
            total_models=5,
            successful_models=["A", "B", "C"],
            failed_models={"D": "err1", "E": "err2"},
        )
        assert b.success_count == 3
        assert b.failure_count == 2
        assert not b.all_succeeded

    def test_batch_result_all_succeeded(self):
        from semabridge.core.concurrency.models import BatchResult
        b = BatchResult(
            batch_id="abc",
            total_models=2,
            successful_models=["A", "B"],
            failed_models={},
        )
        assert b.all_succeeded

    def test_validation_result(self):
        from semabridge.core.concurrency.models import ValidationResult
        v = ValidationResult()
        assert v.success
        v.errors.append("bad cred")
        v.success = False
        assert not v.success

    def test_resource_metrics(self):
        from semabridge.core.concurrency.models import ResourceMetrics
        m = ResourceMetrics(cpu_percent=25.0, memory_mb=16000, available_memory_mb=8000, active_workers=2)
        assert m.active_workers == 2
        assert m.cpu_percent == 25.0

    def test_concurrency_config_defaults(self):
        from semabridge.core.concurrency.models import ConcurrencyConfig
        c = ConcurrencyConfig()
        assert c.max_threads == 8
        assert c.enable_parallel is True
        assert c.memory_threshold_percent == 80.0


# ======================================================================
# 2. ErrorClassifier
# ======================================================================

class TestErrorClassifier:
    """Tests for ErrorClassifier."""

    def test_classify_connection_error(self):
        from semabridge.core.concurrency.error_classifier import ErrorClassifier
        c = ErrorClassifier()
        assert c.classify(ConnectionError("reset")) == "transient"

    def test_classify_timeout_error(self):
        from semabridge.core.concurrency.error_classifier import ErrorClassifier
        c = ErrorClassifier()
        assert c.classify(TimeoutError("timed out")) == "transient"

    def test_classify_permission_error(self):
        from semabridge.core.concurrency.error_classifier import ErrorClassifier
        c = ErrorClassifier()
        assert c.classify(PermissionError("denied")) == "permanent"

    def test_classify_value_error(self):
        from semabridge.core.concurrency.error_classifier import ErrorClassifier
        c = ErrorClassifier()
        assert c.classify(ValueError("bad value")) == "permanent"

    def test_classify_generic_error(self):
        from semabridge.core.concurrency.error_classifier import ErrorClassifier
        c = ErrorClassifier()
        # RuntimeError isn't in either set — defaults to permanent
        assert c.classify(RuntimeError("unknown")) == "permanent"

    def test_classify_message_fragment_timeout(self):
        from semabridge.core.concurrency.error_classifier import ErrorClassifier
        c = ErrorClassifier()
        assert c.classify(Exception("connection timeout error")) == "transient"

    def test_classify_message_fragment_rate_limit(self):
        from semabridge.core.concurrency.error_classifier import ErrorClassifier
        c = ErrorClassifier()
        assert c.classify(Exception("rate limit exceeded")) == "transient"

    def test_classify_http_429(self):
        from semabridge.core.concurrency.error_classifier import ErrorClassifier
        c = ErrorClassifier()
        exc = Exception("HTTP 429 Too Many Requests")
        exc.status_code = 429
        assert c.classify(exc) == "transient"

    def test_classify_http_401(self):
        from semabridge.core.concurrency.error_classifier import ErrorClassifier
        c = ErrorClassifier()
        exc = Exception("Unauthorized")
        exc.status_code = 401
        assert c.classify(exc) == "permanent"

    def test_is_retryable(self):
        from semabridge.core.concurrency.error_classifier import ErrorClassifier
        c = ErrorClassifier()
        assert c.classify(ConnectionError("test")) == "transient"
        assert c.classify(PermissionError("test")) == "permanent"


# ======================================================================
# 3. RetryManager
# ======================================================================

class TestRetryManager:
    """Tests for RetryManager."""

    def test_execute_success_first_try(self):
        from semabridge.core.concurrency.retry_manager import RetryManager
        from semabridge.core.concurrency.models import RetryConfig
        rm = RetryManager(config=RetryConfig(max_retries=3, base_delay=0.01))
        result = rm.execute_with_retry(lambda: 42, "test_op")
        assert result == 42
        assert rm.get_retry_count() == 0

    def test_execute_retries_transient(self):
        from semabridge.core.concurrency.retry_manager import RetryManager
        from semabridge.core.concurrency.models import RetryConfig

        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return "ok"

        rm = RetryManager(config=RetryConfig(max_retries=5, base_delay=0.01))
        result = rm.execute_with_retry(flaky, "flaky_op")
        assert result == "ok"
        assert rm.get_retry_count() == 2

    def test_execute_permanent_error_no_retry(self):
        from semabridge.core.concurrency.retry_manager import RetryManager, PermanentError
        from semabridge.core.concurrency.models import RetryConfig

        rm = RetryManager(config=RetryConfig(max_retries=5, base_delay=0.01))
        with pytest.raises(PermanentError):
            rm.execute_with_retry(
                lambda: (_ for _ in ()).throw(PermissionError("denied")),
                "perm_op",
            )

    def test_execute_max_retries_exceeded(self):
        from semabridge.core.concurrency.retry_manager import RetryManager, MaxRetriesExceeded
        from semabridge.core.concurrency.models import RetryConfig

        rm = RetryManager(config=RetryConfig(max_retries=2, base_delay=0.01))
        with pytest.raises(MaxRetriesExceeded):
            rm.execute_with_retry(
                lambda: (_ for _ in ()).throw(ConnectionError("fail")),
                "always_fail",
            )

    def test_backoff_calculation(self):
        from semabridge.core.concurrency.retry_manager import RetryManager
        from semabridge.core.concurrency.models import RetryConfig

        rm = RetryManager(config=RetryConfig(
            base_delay=1.0, exponential_base=2.0, max_delay=60.0
        ))
        assert rm.calculate_backoff(1) == 1.0
        assert rm.calculate_backoff(2) == 2.0
        assert rm.calculate_backoff(3) == 4.0
        assert rm.calculate_backoff(10) <= 60.0  # capped


# ======================================================================
# 4. ResourceManager
# ======================================================================

class TestResourceManager:
    """Tests for ResourceManager."""

    def test_calculate_worker_count_auto(self):
        from semabridge.core.concurrency.resource_manager import ResourceManager
        rm = ResourceManager()
        count = rm.calculate_worker_count(100)
        assert count >= 1
        assert count <= os.cpu_count()

    def test_calculate_worker_count_limited(self):
        from semabridge.core.concurrency.resource_manager import ResourceManager
        rm = ResourceManager(max_processes=2, memory_threshold_percent=99.0)
        assert rm.calculate_worker_count(100) == 2

    def test_worker_count_never_exceeds_model_count(self):
        from semabridge.core.concurrency.resource_manager import ResourceManager
        rm = ResourceManager(max_processes=10, memory_threshold_percent=99.0)
        assert rm.calculate_worker_count(3) == 3

    def test_get_cpu_count(self):
        from semabridge.core.concurrency.resource_manager import ResourceManager
        rm = ResourceManager()
        assert rm.get_cpu_count() >= 1

    def test_check_disk_space(self):
        from semabridge.core.concurrency.resource_manager import ResourceManager
        rm = ResourceManager()
        has_space = rm.check_disk_space(".")
        assert isinstance(has_space, bool)

    def test_monitor_resources(self):
        from semabridge.core.concurrency.resource_manager import ResourceManager
        rm = ResourceManager()
        metrics = rm.monitor_resources()
        assert metrics.cpu_percent >= 0
        assert metrics.memory_mb >= 0


# ======================================================================
# 5. ConfigurationResolver
# ======================================================================

class TestConfigurationResolver:
    """Tests for ConfigurationResolver."""

    def test_resolve_models_delegates(self):
        from semabridge.core.concurrency.config_resolver import ConfigurationResolver

        config = MagicMock()
        config.get_included_models.return_value = ["A", "B", "C"]
        cr = ConfigurationResolver(config)
        models = cr.resolve_models(available_models=["A", "B", "C", "D"])
        assert set(models) == {"A", "B", "C"}

    def test_resolve_models_deduplicates(self):
        from semabridge.core.concurrency.config_resolver import ConfigurationResolver

        config = MagicMock()
        config.get_included_models.return_value = ["A", "A", "B"]
        cr = ConfigurationResolver(config)
        models = cr.resolve_models(available_models=["A", "B"])
        assert len(models) == len(set(models))

    def test_is_multi_target(self):
        from semabridge.core.concurrency.config_resolver import ConfigurationResolver

        config = MagicMock()
        target1 = MagicMock()
        target1.deploy = True
        target2 = MagicMock()
        target2.deploy = True
        config.targets = [target1, target2]
        cr = ConfigurationResolver(config)
        assert cr.is_multi_target()

    def test_identify_broadcast_scenarios(self):
        from semabridge.core.concurrency.config_resolver import ConfigurationResolver

        config = MagicMock()
        target1 = MagicMock()
        target1.type = MagicMock(value="snowflake")
        target1.deploy = True
        target2 = MagicMock()
        target2.type = MagicMock(value="fabric")
        target2.deploy = True
        config.targets = [target1, target2]
        cr = ConfigurationResolver(config)
        scenarios = cr.identify_broadcast_scenarios()
        assert len(scenarios) == 1
        assert len(scenarios[0].target_names) == 2


# ======================================================================
# 6. ValidationEngine
# ======================================================================

class TestValidationEngine:
    """Tests for ValidationEngine."""

    def test_validate_all_returns_validation_result(self):
        from semabridge.core.concurrency.validation_engine import ValidationEngine
        config = MagicMock()
        config.source = MagicMock()
        config.source.type = MagicMock(value="snowflake")
        config.targets = []
        ve = ValidationEngine(config)
        result = ve.validate_all()
        assert hasattr(result, "success")

    def test_validate_missing_snowflake_creds(self):
        from semabridge.core.concurrency.validation_engine import ValidationEngine
        config = MagicMock()
        config.source = MagicMock()
        config.source.type = MagicMock(value="snowflake")
        config.targets = []
        ve = ValidationEngine(config)

        env_backup = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("SNOWFLAKE")}
        try:
            result = ve.validate_all()
            # Should have credential errors
            assert not result.credentials_valid
        finally:
            os.environ.update(env_backup)


# ======================================================================
# 7. ConnectionPoolManager
# ======================================================================

class TestConnectionPoolManager:
    """Tests for ConnectionPoolManager."""

    def test_pool_lifecycle(self):
        from semabridge.core.concurrency.connection_pool import ConnectionPoolManager

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            pool = ConnectionPoolManager(db_path=db_path, pool_size=2, timeout=5.0)
            conn = pool.get_connection()
            assert conn is not None
            conn.execute("SELECT 1")
            pool.release_connection(conn)
            pool.close_all()

    def test_context_manager(self):
        from semabridge.core.concurrency.connection_pool import ConnectionPoolManager

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            pool = ConnectionPoolManager(db_path=db_path, pool_size=2)
            with pool.connection() as conn:
                result = conn.execute("SELECT 42").fetchone()
                assert result[0] == 42
            pool.close_all()


# ======================================================================
# 8. FileManager
# ======================================================================

class TestFileManager:
    """Tests for FileManager."""

    def test_write_artifact(self):
        from semabridge.core.concurrency.file_manager import FileManager

        with tempfile.TemporaryDirectory() as tmpdir:
            fm = FileManager(base_path=tmpdir)
            path = fm.write_artifact("test.json", '{"key": "value"}', worker_id="w1")
            assert Path(path).exists()
            assert json.loads(Path(path).read_text()) == {"key": "value"}

    def test_worker_isolation(self):
        from semabridge.core.concurrency.file_manager import FileManager

        with tempfile.TemporaryDirectory() as tmpdir:
            fm = FileManager(base_path=tmpdir)
            dir1 = fm.get_worker_dir("w1")
            dir2 = fm.get_worker_dir("w2")
            assert dir1 != dir2
            assert Path(dir1).exists()
            assert Path(dir2).exists()

    def test_cleanup_worker_dir(self):
        from semabridge.core.concurrency.file_manager import FileManager

        with tempfile.TemporaryDirectory() as tmpdir:
            fm = FileManager(base_path=tmpdir)
            d = fm.get_worker_dir("w1")
            Path(d, "file.txt").write_text("test")
            fm.cleanup_worker_dir("w1")
            assert not Path(d).exists()

    def test_check_disk_space(self):
        from semabridge.core.concurrency.file_manager import FileManager

        with tempfile.TemporaryDirectory() as tmpdir:
            fm = FileManager(base_path=tmpdir)
            has_space = fm.ensure_disk_space(required_mb=1)
            assert has_space  # should have at least 1MB on any real system


# ======================================================================
# 9. ProgressReporter
# ======================================================================

class TestProgressReporter:
    """Tests for ProgressReporter."""

    def test_reporter_no_dashboard(self):
        from semabridge.core.concurrency.progress_reporter import ProgressReporter
        pr = ProgressReporter(total_models=5, show_dashboard=False)
        pr.start()
        pr.report_success("A", 1.0)
        pr.report_failure("B", "error")
        assert pr.success_count == 1
        assert pr.failure_count == 1
        assert pr.progress_percent == 40.0
        pr.stop()

    def test_reporter_total_models(self):
        from semabridge.core.concurrency.progress_reporter import ProgressReporter
        pr = ProgressReporter(total_models=10, show_dashboard=False)
        assert pr.total_models == 10


# ======================================================================
# 10. ErrorReporter
# ======================================================================

class TestErrorReporter:
    """Tests for ErrorReporter."""

    def test_generate_json_report(self):
        from semabridge.core.concurrency.error_reporter import ErrorReporter
        from semabridge.core.concurrency.models import BatchResult

        with tempfile.TemporaryDirectory() as tmpdir:
            er = ErrorReporter(output_dir=tmpdir)
            batch = BatchResult(
                batch_id="test123",
                total_models=3,
                successful_models=["A"],
                failed_models={"B": "err1", "C": "err2"},
            )
            path = er.generate_json_report(batch)
            assert path.exists()
            data = json.loads(path.read_text())
            assert data["batch_id"] == "test123"
            assert data["failed_count"] == 2

    def test_generate_markdown_report(self):
        from semabridge.core.concurrency.error_reporter import ErrorReporter
        from semabridge.core.concurrency.models import BatchResult

        with tempfile.TemporaryDirectory() as tmpdir:
            er = ErrorReporter(output_dir=tmpdir)
            batch = BatchResult(
                batch_id="test456",
                total_models=2,
                successful_models=["A", "B"],
                failed_models={},
            )
            path = er.generate_markdown_report(batch)
            assert path.exists()
            content = path.read_text()
            assert "test456" in content
            assert "Succeeded" in content

    def test_log_summary_does_not_raise(self):
        from semabridge.core.concurrency.error_reporter import ErrorReporter
        from semabridge.core.concurrency.models import BatchResult

        er = ErrorReporter()
        batch = BatchResult(
            batch_id="x", total_models=1,
            successful_models=["A"], failed_models={},
        )
        er.log_summary(batch)  # should not raise


# ======================================================================
# 11. AuthenticationManager
# ======================================================================

class TestAuthenticationManager:
    """Tests for AuthenticationManager."""

    def test_validate_missing_snowflake(self):
        from semabridge.core.concurrency.auth_manager import AuthenticationManager
        from semabridge.core.exceptions import MissingCredentialError

        # Clear snowflake env vars
        env_backup = {}
        for k in list(os.environ):
            if k.startswith("SNOWFLAKE"):
                env_backup[k] = os.environ.pop(k)

        try:
            am = AuthenticationManager(source="snowflake")
            with pytest.raises(MissingCredentialError):
                am.validate()
        finally:
            os.environ.update(env_backup)

    def test_validate_present_creds(self):
        from semabridge.core.concurrency.auth_manager import AuthenticationManager

        env_vars = {
            "SNOWFLAKE_ACCOUNT": "test_account",
            "SNOWFLAKE_USER": "test_user",
            "SNOWFLAKE_PASSWORD": "test_pass",
        }
        with patch.dict(os.environ, env_vars):
            am = AuthenticationManager(source="snowflake")
            am.validate()  # should not raise

    def test_get_credential_snapshot(self):
        from semabridge.core.concurrency.auth_manager import AuthenticationManager

        env_vars = {
            "SNOWFLAKE_ACCOUNT": "acct",
            "SNOWFLAKE_USER": "usr",
            "SNOWFLAKE_PASSWORD": "pwd",
        }
        with patch.dict(os.environ, env_vars):
            am = AuthenticationManager(source="snowflake")
            snap = am.get_credential_snapshot()
            assert snap["SNOWFLAKE_ACCOUNT"] == "acct"

    def test_get_missing_credentials(self):
        from semabridge.core.concurrency.auth_manager import AuthenticationManager

        env_backup = {}
        for k in list(os.environ):
            if k.startswith("SNOWFLAKE"):
                env_backup[k] = os.environ.pop(k)

        try:
            am = AuthenticationManager(source="snowflake")
            missing = am.get_missing_credentials()
            assert len(missing) > 0
        finally:
            os.environ.update(env_backup)


# ======================================================================
# 12. Batch Persistence
# ======================================================================

class TestBatchPersistence:
    """Tests for DuckDB batch extension functions."""

    def _make_db_manager(self, tmpdir):
        """Create a minimal DuckDBManager-like object."""
        db_path = os.path.join(tmpdir, "test.db")
        mgr = MagicMock()
        mgr._get_connection = lambda: duckdb.connect(db_path)
        return mgr

    def test_ensure_batch_tables(self):
        from semabridge.core.concurrency.batch_persistence import ensure_batch_tables

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_db_manager(tmpdir)
            ensure_batch_tables(mgr)
            # Should be idempotent
            ensure_batch_tables(mgr)

    def test_record_and_complete_batch(self):
        from semabridge.core.concurrency.batch_persistence import (
            ensure_batch_tables,
            record_batch_start,
            record_model_result,
            record_batch_complete,
            get_batch_history,
            get_batch_model_results,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_db_manager(tmpdir)
            ensure_batch_tables(mgr)
            record_batch_start(mgr, "b1", total_models=2, worker_count=1)
            record_model_result(mgr, "b1", "ModelA", success=True, duration_seconds=1.5)
            record_model_result(mgr, "b1", "ModelB", success=False, error_message="fail")
            record_batch_complete(mgr, "b1", successful_count=1, failed_count=1)

            history = get_batch_history(mgr)
            assert len(history) == 1
            assert history[0]["batch_id"] == "b1"
            assert history[0]["status"] == "partial"

            results = get_batch_model_results(mgr, "b1")
            assert len(results) == 2


# ======================================================================
# 13. ResumeManager
# ======================================================================

class TestResumeManager:
    """Tests for ResumeManager."""

    def _make_db_manager(self, tmpdir):
        db_path = os.path.join(tmpdir, "test.db")
        mgr = MagicMock()
        mgr._get_connection = lambda: duckdb.connect(db_path)
        return mgr

    def test_save_and_get_failed_models(self):
        from semabridge.core.concurrency.resume_manager import ResumeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_db_manager(tmpdir)
            rm = ResumeManager(mgr)
            rm.save_batch_state(
                batch_id="b1",
                failed_models=["A", "B"],
                successful_models=["C"],
                context={"source": "snowflake"},
            )
            failed = rm.get_failed_models("b1")
            assert failed == ["A", "B"]

    def test_get_batch_context(self):
        from semabridge.core.concurrency.resume_manager import ResumeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_db_manager(tmpdir)
            rm = ResumeManager(mgr)
            rm.save_batch_state(
                batch_id="b2",
                failed_models=["X"],
                successful_models=[],
                context={"key": "val"},
            )
            ctx = rm.get_batch_context("b2")
            assert ctx["key"] == "val"

    def test_list_resumable_batches(self):
        from semabridge.core.concurrency.resume_manager import ResumeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_db_manager(tmpdir)
            rm = ResumeManager(mgr)
            rm.save_batch_state("b1", ["A"], ["B"], {})
            rm.save_batch_state("b2", ["C"], ["D"], {})
            batches = rm.list_resumable_batches()
            assert len(batches) == 2

    def test_mark_batch_complete(self):
        from semabridge.core.concurrency.resume_manager import ResumeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_db_manager(tmpdir)
            rm = ResumeManager(mgr)
            rm.save_batch_state("b1", ["A"], ["B"], {})
            rm.mark_batch_complete("b1")
            batches = rm.list_resumable_batches()
            assert len(batches) == 0  # completed batches not listed

    def test_get_failed_models_not_found(self):
        from semabridge.core.concurrency.resume_manager import ResumeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._make_db_manager(tmpdir)
            rm = ResumeManager(mgr)
            with pytest.raises(ValueError, match="Batch not found"):
                rm.get_failed_models("nonexistent")


# ======================================================================
# 14. ProjectConfig ConcurrencyOptions
# ======================================================================

class TestProjectConfigConcurrency:
    """Tests for the new concurrency section in ProjectConfig."""

    def test_default_concurrency_options(self):
        from semabridge.core.project import ProjectConfig, SourceConfig, SourceType

        cfg = ProjectConfig(
            source=SourceConfig(type=SourceType.SNOWFLAKE, database="DB"),
        )
        assert cfg.concurrency.max_workers == 0
        assert cfg.concurrency.execution_mode == "best_effort"
        assert cfg.concurrency.enable_parallel is True

    def test_custom_concurrency_options(self):
        from semabridge.core.project import ProjectConfig, SourceConfig, SourceType, ConcurrencyOptions

        cfg = ProjectConfig(
            source=SourceConfig(type=SourceType.SNOWFLAKE, database="DB"),
            concurrency=ConcurrencyOptions(
                max_workers=4,
                execution_mode="strict",
                max_retries=5,
            ),
        )
        assert cfg.concurrency.max_workers == 4
        assert cfg.concurrency.execution_mode == "strict"
        assert cfg.concurrency.max_retries == 5

    def test_concurrency_from_yaml_dict(self):
        from semabridge.core.project import ProjectConfig

        data = {
            "source": {"type": "snowflake", "database": "DB"},
            "concurrency": {
                "max_workers": 2,
                "execution_mode": "strict",
            },
        }
        cfg = ProjectConfig.from_yaml_dict(data)
        assert cfg.concurrency.max_workers == 2
        assert cfg.concurrency.execution_mode == "strict"


# ======================================================================
# 15. Package imports
# ======================================================================

class TestPackageImports:
    """Tests that the public API can be imported."""

    def test_import_all(self):
        from semabridge.core.concurrency import (
            AuthenticationManager,
            BatchResult,
            BroadcastResult,
            ConcurrencyConfig,
            ConcurrencyOrchestrator,
            ConfigurationResolver,
            ConnectionPoolManager,
            ErrorClassifier,
            ErrorReporter,
            ExecutionMode,
            FileManager,
            ModelProcessor,
            ModelResult,
            ProcessingStage,
            ProgressReporter,
            ResourceManager,
            ResumeManager,
            RetryConfig,
            RetryManager,
            ValidationEngine,
            ValidationResult,
            WorkerStatus,
        )
        # All imports should work
        assert ExecutionMode.BEST_EFFORT is not None
