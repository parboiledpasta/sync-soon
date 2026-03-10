"""
SemaBridge Core Engine.

Implements the "One Source, Many Targets" broadcasting framework with:
- Single extraction, single conversion workflow
- Multi-tiered concurrency (ProcessPoolExecutor for conversion, ThreadPoolExecutor for broadcast)
- Load shedding and bulkhead isolation for target resilience
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Semaphore
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from semabridge.core.project import ProjectConfig, SourceConfig, TargetConfig
from semabridge.utils.logger import get_logger
from semabridge.utils.model_dedup import (
    deduplicate_models,
    deduplicate_model_names,
)

logger = get_logger(__name__)


class ExecutionPhase(str, Enum):
    """Execution phases for the SemaBridge engine."""
    DISCOVERY = "discovery"
    GOVERNANCE = "governance"
    EXTRACTION = "extraction"
    CONVERSION = "conversion"
    VALIDATION = "validation"
    BROADCAST = "broadcast"
    CLEANUP = "cleanup"


@dataclass
class BroadcastResult:
    """Result of broadcasting to a single target."""
    target: TargetConfig
    success: bool
    message: str = ""
    duration_ms: float = 0.0
    error: Optional[Exception] = None


@dataclass
class EngineResult:
    """Complete result of an engine execution."""
    success: bool
    models_processed: int = 0
    targets_succeeded: int = 0
    targets_failed: int = 0
    broadcast_results: List[BroadcastResult] = field(default_factory=list)
    total_duration_ms: float = 0.0
    phase_timings: Dict[ExecutionPhase, float] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    changed_models: List[str] = field(default_factory=list)


class SourceConnectorBase:
    """
    Base class for source connectors.
    
    All source connectors must implement discover() and extract() methods.
    """
    
    def discover(self, pattern: str = "*") -> List[str]:
        """
        Discover available models matching the pattern.
        
        Args:
            pattern: Glob pattern for model selection
            
        Returns:
            List of model names/IDs available in the source
        """
        raise NotImplementedError("Subclasses must implement discover()")

    def validate_permissions(self) -> List[str]:
        """Validate RBAC permissions."""
        return []

    @property
    def max_concurrency(self) -> int:
        """Max concurrency limit."""
        return 5
    
    def extract(self, model_id: str, exclusions: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        """
        Extract metadata for a single model.
        
        Args:
            model_id: Model identifier
            exclusions: Optional dict of exclusion patterns by type
            
        Returns:
            Raw metadata dictionary
        """
        raise NotImplementedError("Subclasses must implement extract()")


class TargetConnectorBase:
    """
    Base class for target connectors.
    
    All target connectors must implement deploy() method.
    """
    
    def deploy(self, sml_model: Any) -> bool:
        """
        Deploy an SML model to this target.
        
        Args:
            sml_model: The SML model to deploy
            
        Returns:
            True if deployment succeeded
        """
        raise NotImplementedError("Subclasses must implement deploy()")


class SemaBridgeEngine:
    """
    Core engine for semantic model broadcasting.
    
    Implements the "One Source, Many Targets" pattern with:
    - Single extraction per model
    - Single SML conversion per model
    - Parallel broadcast to all targets
    - Load shedding via semaphores
    - Bulkhead isolation per target
    """
    
    # Concurrency configuration
    MAX_CONVERSION_WORKERS = 4  # CPU-bound
    MAX_EXTRACTION_WORKERS = 5  # I/O-bound Fabric API calls
    MAX_BROADCAST_WORKERS = 10  # I/O-bound
    TARGET_SEMAPHORE_LIMIT = 5  # Max concurrent calls per target type
    
    def __init__(
        self,
        config: ProjectConfig,
        source_connector: Optional[SourceConnectorBase] = None,
        target_connectors: Optional[Dict[str, TargetConnectorBase]] = None,
        duckdb_manager: Optional[Any] = None,
    ):
        """
        Initialize the engine.
        
        Args:
            config: Project configuration
            source_connector: Optional pre-configured source connector
            target_connectors: Optional dict of target type -> connector
            duckdb_manager: Optional DuckDB manager for versioning
        """
        self.config = config
        self.source_connector = source_connector
        self.target_connectors = target_connectors or {}
        self.duckdb_manager = duckdb_manager
        
        # Semaphores for load shedding (per target type)
        self._target_semaphores: Dict[str, Semaphore] = {}
        
        # Phase timing
        self._phase_timings: Dict[ExecutionPhase, float] = {}
    
    def _get_or_create_semaphore(self, target: TargetConfig) -> Semaphore:
        """Get or create a semaphore for a target (bulkhead isolation)."""
        target_type_value = target.type.value if hasattr(target.type, 'value') else str(target.type)
        key = f"{target_type_value}:{target.database}:{target.schema_name}"
        if key not in self._target_semaphores:
            connector = self._get_or_create_target_connector(target)
            limit = getattr(connector, "max_concurrency", self.TARGET_SEMAPHORE_LIMIT)
            self._target_semaphores[key] = Semaphore(limit)
        return self._target_semaphores[key]

    def _validate_governance(self) -> None:
        """
        Validate RBAC permissions across all involved connectors.
        
        This phase ensures that the principal has necessary access
        BEFORE starting expensive extraction/broadcast operations.
        """
        logger.info("Starting Governance Check (RBAC validation)...")
        all_warnings: List[str] = []
        
        # 1. Source Connector RBAC
        if self.source_connector:
            warnings = self.source_connector.validate_permissions()
            all_warnings.extend([f"Source: {w}" for w in warnings])
            
        # 2. Target Connector RBAC
        for target in self.config.targets:
            connector = self._get_or_create_target_connector(target)
            warnings = connector.validate_permissions()
            all_warnings.extend([f"Target {target.type.value}: {w}" for w in warnings])
            
        if all_warnings:
            for warning in all_warnings:
                logger.warning(warning)
    
    def _time_phase(self, phase: ExecutionPhase):
        """Context manager to time a phase."""
        class PhaseTimer:
            def __init__(timer_self):
                timer_self.start = 0.0
            
            def __enter__(timer_self):
                timer_self.start = time.perf_counter()
                return timer_self
            
            def __exit__(timer_self, *args):
                elapsed = (time.perf_counter() - timer_self.start) * 1000
                self._phase_timings[phase] = elapsed
                logger.debug(f"Phase {phase.value} completed in {elapsed:.2f}ms")
        
        return PhaseTimer()
    
    
    def validate_run(self) -> EngineResult:
        """
        Run validation only (Discovery -> Extraction -> Conversion -> Validation).
        Does NOT broadcast to targets.
        """
        start_time = time.time()
        logger.info("Starting Schema Validation Run...")

        # Phase 1: Discovery
        with self._time_phase(ExecutionPhase.DISCOVERY):
            start_discovery = time.time()
            discovered_models = self._discover_models()
            
            # Filter included/excluded models
            final_models = self.config.get_included_models(discovered_models)
            logger.info(f"Discovered {len(discovered_models)} models, {len(final_models)} after filtering")
            
        if not final_models:
            logger.warning("No models found to validate")
            return EngineResult(success=True)

        # Phase 2: Extraction
        with self._time_phase(ExecutionPhase.EXTRACTION):
            extracted_metadata = self._extract_models(final_models)
        
        # Phase 3: Conversion
        with self._time_phase(ExecutionPhase.CONVERSION):
            sml_models = self._convert_models(extracted_metadata)
            logger.info(f"Converted {len(sml_models)} models to SML")

        # Phase 3.5: Validation
        with self._time_phase(ExecutionPhase.VALIDATION):
            valid_models = self._validate_models(sml_models)
            
        duration = (time.time() - start_time) * 1000
        success = len(valid_models) == len(sml_models)
        
        return EngineResult(
            success=success,
            models_processed=len(sml_models),
            targets_succeeded=0,
            targets_failed=0,
            total_duration_ms=duration,
            phase_timings=self._phase_timings,
            errors=[] if success else [f"{len(sml_models) - len(valid_models)} models failed validation"]
        )

    def execute(self) -> EngineResult:
        """
        Execute the full broadcast pipeline.
        
        Steps:
        1. DISCOVERY: List models from source, apply inclusion/exclusion patterns
        2. EXTRACTION: Extract metadata once per model
        3. CONVERSION: Convert to SML/OSI (CPU-bound, process pool)
        4. BROADCAST: Deploy to all targets (I/O-bound, thread pool)
        5. CLEANUP: Finalize and log results
        
        Returns:
            EngineResult with success status and details
        """
        import os
        import asyncio
        if os.getenv("SEMABRIDGE_ORCHESTRATOR", "local").lower() == "temporal":
            logger.info("Delegating execution to Temporal Orchestrator (Docker)...")
            # If there's a running loop (e.g., from UI checks), run in executor
            try:
                loop = asyncio.get_running_loop()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(lambda: asyncio.run(self._execute_temporal())).result()
            except RuntimeError:
                return asyncio.run(self._execute_temporal())

        start_time = time.perf_counter()
        errors: List[str] = []
        broadcast_results: List[BroadcastResult] = []
        
        try:
            # Phase 1: Discovery
            with self._time_phase(ExecutionPhase.DISCOVERY):
                discovered_models = self._discover_models()
                final_models = self.config.get_included_models(discovered_models)
                logger.info(f"Discovered {len(discovered_models)} models, {len(final_models)} after filtering")
            
            if not final_models:
                return EngineResult(
                    success=True,
                    models_processed=0,
                    errors=["No models matched the inclusion/exclusion criteria"]
                )

            # Phase 1.5: Name-level deduplication (Module 3)
            # Catches "Regional_Sales_Sample" vs "Regional Sales Sample"
            # before we waste extraction API calls on duplicates.
            final_models, name_dupes = deduplicate_model_names(final_models)
            if name_dupes:
                for g in name_dupes:
                    errors.append(
                        f"Duplicate model names detected "
                        f"(canonical='{g.canonical_name}'): "
                        f"kept='{g.kept}', dropped={g.dropped}"
                    )
                logger.info(
                    f"After name-dedup: {len(final_models)} unique models"
                )

            # Phase 2: Governance (RBAC Check)
            with self._time_phase(ExecutionPhase.GOVERNANCE):
                self._validate_governance()
            
            # Phase 3: Extraction (single pass per model)
            with self._time_phase(ExecutionPhase.EXTRACTION):
                source_type_value = (
                    self.config.source.type.value
                    if hasattr(self.config.source.type, 'value')
                    else str(self.config.source.type)
                )
                extracted_metadata = self._extract_models(final_models)
            
            # Phase 3b: Conversion
            with self._time_phase(ExecutionPhase.CONVERSION):
                if source_type_value == "snowflake":
                    # Snowflake: combine ALL selected tables into ONE SML model.
                    # A Fabric semantic model is a star schema, not one table.
                    sml_models = self._convert_snowflake_combined(
                        final_models, extracted_metadata
                    )
                else:
                    # Fabric/other: each model is independent
                    sml_models = self._convert_models(extracted_metadata)
                logger.info(f"Converted {len(sml_models)} models to SML")
            
            if not sml_models:
                logger.warning("No SML models generated")
                return EngineResult(success=True)

            # Phase 3.5: Physical Validation
            with self._time_phase(ExecutionPhase.VALIDATION):
                sml_models = self._validate_models(sml_models)
                if not sml_models:
                    logger.error("All models failed validation")
                    return EngineResult(success=False, errors=["Physical validation failed for all models"])

            # Phase 4: Broadcast (I/O-bound parallel)
            with self._time_phase(ExecutionPhase.BROADCAST):
                changed_models = []
                # Before broadcasting, version the models if DuckDB is enabled
                if self.duckdb_manager:
                    logger.info("Versioning models in DuckDB...")
                    for model_id, sml_model in sml_models.items():
                        try:
                            # Use displayName for name if available
                            name = getattr(sml_model, 'label', model_id)
                            workspace_id = getattr(self.config.source, 'workspace_id', None) or ""
                            
                            # For Snowflake sources, fall back to Fabric target workspace from .env
                            if not workspace_id:
                                try:
                                    from semabridge.core.settings import get_settings
                                    workspace_id = get_settings().fabric.workspace_id or ""
                                except Exception:
                                    workspace_id = ""
                            
                            source_type_value = self.config.source.type.value if hasattr(self.config.source.type, 'value') else str(self.config.source.type)
                            self.duckdb_manager.ensure_project(
                                project_id=model_id,
                                name=name,
                                workspace_id=workspace_id,
                                adapter=source_type_value
                            )
                            
                            sml_dict = sml_model.model_dump(mode='json')
                            committed, snapshot_id = self.duckdb_manager.commit_model(
                                project_id=model_id,
                                sml_json=sml_dict,
                                tag=self.config.version_tag,
                                initiated_by="engine"
                            )
                            if committed:
                                logger.info(f"Snapshot committed for {model_id}: {snapshot_id}")
                                changed_models.append(model_id)
                        except Exception as e:
                            logger.error(f"Failed to version model {model_id}: {e}")

                broadcast_results = self._broadcast_to_targets(list(sml_models.values()))
            
            # Phase 5: Cleanup
            with self._time_phase(ExecutionPhase.CLEANUP):
                self._cleanup()
            
            # Compute summary
            succeeded = [r for r in broadcast_results if r.success]
            failed = [r for r in broadcast_results if not r.success]
            
            for r in failed:
                errors.append(f"Target {r.target.type.value}: {r.message}")
            
            total_duration = (time.perf_counter() - start_time) * 1000
            
            return EngineResult(
                success=len(failed) == 0,
                models_processed=len(final_models),
                targets_succeeded=len(succeeded),
                targets_failed=len(failed),
                broadcast_results=broadcast_results,
                total_duration_ms=total_duration,
                phase_timings=self._phase_timings.copy(),
                errors=errors,
                changed_models=changed_models,
            )
            
        except Exception as e:
            logger.exception("Engine execution failed")
            total_duration = (time.perf_counter() - start_time) * 1000
            return EngineResult(
                success=False,
                total_duration_ms=total_duration,
                phase_timings=self._phase_timings.copy(),
                errors=[str(e)],
            )

    async def _execute_temporal(self) -> EngineResult:
        import uuid
        import os
        import time
        from temporalio.client import Client
        from semabridge.orchestration.temporal.workflows import SyncWorkflowInput
        
        start_time = time.perf_counter()
        
        discovered_models = self._discover_models()
        final_models = self.config.get_included_models(discovered_models)
        final_models, _ = deduplicate_model_names(final_models)
        
        if not final_models:
            return EngineResult(success=True, models_processed=0)
            
        client = await Client.connect(os.getenv("TEMPORAL_HOST", "localhost:7233"), namespace="default")
        
        inputs = []
        for mn in final_models:
            inputs.append(SyncWorkflowInput(
                tenant_id=os.getenv("FABRIC_TENANT_ID", "default_tenant"),
                model_id=mn,
                database=os.getenv("SNOWFLAKE_DATABASE", "ANALYTICS_DB"),
                schema=os.getenv("SNOWFLAKE_SCHEMA", "SEMANTIC_LAYER"),
                snowflake_account=os.getenv("SNOWFLAKE_ACCOUNT", ""),
                fabric_workspace_id=os.getenv("FABRIC_WORKSPACE_ID", ""),
            ))
            
        batch_id = f"batch-sync-{uuid.uuid4().hex[:8]}"
        handle = await client.start_workflow(
            "BatchSyncWorkflow",
            inputs,
            id=batch_id,
            task_queue=os.getenv("TASK_QUEUE", "semabridge-sync"),
        )
        
        results = await handle.result()
        
        succeeded = 0
        changed = []
        errors = []
        for r in results:
            if isinstance(r, dict):
                p = r.get("phase")
                e = r.get("error", "")
                m = r.get("model_id")
                c = r.get("changes_detected", 0)
            else:
                p = getattr(r, "phase", "")
                e = getattr(r, "error", "")
                m = getattr(r, "model_id", "")
                c = getattr(r, "changes_detected", 0)
                
            if p == "completed":
                succeeded += 1
                if c > 0: changed.append(m)
            else:
                if "Extraction failed" not in str(e) and "No tables match" not in str(e):
                    errors.append(f"{m}: {e}")
                else:
                    # Treat skipping gracefully, but still doesn't count as exact target_success
                    succeeded += 1  

        return EngineResult(
            success=len(errors) == 0,
            models_processed=len(final_models),
            targets_succeeded=succeeded,
            targets_failed=len(final_models) - succeeded,
            total_duration_ms=(time.perf_counter() - start_time) * 1000,
            errors=errors,
            changed_models=changed
        )
    
    def _discover_models(self) -> List[str]:
        """Discover models from the source using the configured pattern."""
        if self.source_connector is None:
            self.source_connector = self._create_source_connector()
        
        pattern = self.config.source.model
        logger.debug(f"Discovering models with pattern: {pattern}")
        
        return self.source_connector.discover(pattern)
    
    def _extract_models(self, model_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Extract metadata for all models.

        Uses a :class:`ThreadPoolExecutor` to issue Fabric REST API calls
        in parallel (I/O-bound).  For a single model the overhead of the
        thread pool is skipped.
        
        Args:
            model_ids: List of model IDs to extract
            
        Returns:
            Dict mapping model_id to extracted metadata
        """
        results: Dict[str, Dict[str, Any]] = {}
        
        # Build exclusion rules from config
        exclusions = self._build_exclusion_dict()

        def _extract_one(model_id: str) -> Tuple[str, Optional[Dict[str, Any]]]:
            """Extract a single model; returns (model_id, metadata|None)."""
            try:
                logger.info(f"Extracting metadata for model: {model_id}")
                metadata = self.source_connector.extract(model_id, exclusions)
                return (model_id, metadata)
            except Exception as e:
                logger.error(f"Failed to extract model {model_id}: {e}")
                return (model_id, None)

        if len(model_ids) <= 1:
            # Fast path — no thread pool needed
            for model_id in model_ids:
                mid, data = _extract_one(model_id)
                if data is not None:
                    results[mid] = data
        else:
            workers = min(len(model_ids), self.MAX_EXTRACTION_WORKERS)
            logger.info(
                f"Parallelising extraction of {len(model_ids)} models "
                f"with {workers} workers"
            )
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_extract_one, mid): mid
                    for mid in model_ids
                }
                for future in as_completed(futures):
                    mid, data = future.result()
                    if data is not None:
                        results[mid] = data

        return results
    
    def _build_exclusion_dict(self) -> Dict[str, List[str]]:
        """Build exclusion dictionary from config options."""
        exclusions = {
            "tables": [],
            "columns": [],
            "measures": [],
        }
        
        for exclusion in self.config.options.get_semantic_exclusions():
            if exclusion.exclusion_type.value == "table":
                exclusions["tables"].append(exclusion.pattern)
            elif exclusion.exclusion_type.value == "column":
                exclusions["columns"].append(exclusion.pattern)
            elif exclusion.exclusion_type.value == "measure":
                exclusions["measures"].append(exclusion.pattern)
        
        return exclusions
    
    def _convert_models(self, metadata: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Convert extracted metadata to OSI format.
        
        Uses ThreadPoolExecutor for CPU-bound conversion.
        
        Args:
            metadata: Dict mapping model_id to raw metadata
            
        Returns:
            Dict mapping model_id to OSI model objects
        """
        osi_models = {}
        
        # Note: For large batches, we'd use ProcessPoolExecutor here
        # For now, sequential conversion with option to parallelize
        if len(metadata) <= 2:
            # Sequential for small batches
            for model_id, data in metadata.items():
                try:
                    osi = self._convert_single(model_id, data)
                    if osi:
                        osi_models[model_id] = osi
                except Exception as e:
                    logger.error(f"Conversion failed for {model_id}: {e}")
        else:
            # Parallel conversion for larger batches
            # Use ThreadPoolExecutor instead of ProcessPoolExecutor to avoid pickling 
            # errors (like '_thread.lock') that occur when 'self' is serialized on Windows.
            with ThreadPoolExecutor(max_workers=self.MAX_CONVERSION_WORKERS) as executor:
                futures = {
                    executor.submit(self._convert_single, model_id, data): model_id
                    for model_id, data in metadata.items()
                }
                
                for future in as_completed(futures):
                    model_id = futures[future]
                    try:
                        osi = future.result()
                        if osi:
                            osi_models[model_id] = osi
                    except Exception as e:
                        logger.error(f"Conversion failed for {model_id}: {e}")
        
        logger.info(f"Converted {len(osi_models)} models to OSI")
        return osi_models
    
    def _convert_snowflake_combined(
        self,
        selected_tables: List[str],
        extracted_metadata: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Combine all selected Snowflake tables into ONE SML model.
        
        A Fabric semantic model is a star schema with multiple tables
        (datasets), not one table per model. This takes all selected
        table names and produces a single SML model containing all of them.
        
        When the selected items are **semantic views** (not physical tables),
        the filter is skipped so all underlying physical tables participate
        in the star schema, and the SV's DDL is used for authoritative
        relationships and measures.
        
        Args:
            selected_tables: List of table/semantic-view names the user selected
            extracted_metadata: Dict mapping name -> metadata (from extract)
            
        Returns:
            Dict mapping model_id -> SML model (single entry)
        """
        if not extracted_metadata:
            return {}
        
        # All extractions return the same full metadata — use the first one
        first_metadata = next(iter(extracted_metadata.values()))
        
        # Check if the selected items are semantic views (i.e. they don't
        # appear as physical table names in the extracted metadata).
        # If so, use ALL underlying tables instead of filtering.
        physical_tables = set(first_metadata.get("tables", {}).keys())
        selected_are_svs = not any(t in physical_tables for t in selected_tables)
        
        if selected_are_svs:
            logger.info(
                f"Selected items are semantic views — using all "
                f"{len(physical_tables)} underlying physical tables"
            )
            include_tables = None  # Use all tables
            
            # Store the SV names so _convert_snowflake_to_sml can use DDL
            first_metadata["_selected_sv_names"] = selected_tables
        else:
            include_tables = set(selected_tables)
        
        # Use config model_name or combine table names
        model_name = self.config.model_name or "_".join(selected_tables[:3])
        if len(selected_tables) > 3:
            model_name += f"_+{len(selected_tables) - 3}"
        
        try:
            sml_model = self._convert_snowflake_to_sml(
                model_id=model_name,
                metadata=first_metadata,
                include_tables=include_tables,
            )
            if sml_model:
                return {model_name: sml_model}
        except Exception as e:
            logger.error(f"Snowflake combined conversion failed: {e}")
        
        return {}
    
    def _convert_single(self, model_id: str, metadata: Dict[str, Any]) -> Any:
        """
        Convert a single model's metadata to SML.

        Routes to the correct converter based on source type:
        - Snowflake: SMLAssembler (metadata → SML directly)
        - Fabric: TMSLToOSIConverter → OSI pipeline
        """
        from semabridge.core.project import SourceType
        
        source_type_value = (
            self.config.source.type.value
            if hasattr(self.config.source.type, 'value')
            else str(self.config.source.type)
        )
        
        logger.debug(f"Converting {model_id} (source={source_type_value})")
        
        if source_type_value == "snowflake":
            return self._convert_snowflake_to_sml(model_id, metadata)
        else:
            return self._convert_fabric_to_osi(model_id, metadata)
    
    def _convert_snowflake_to_sml(
        self,
        model_id: str,
        metadata: Dict[str, Any],
        include_tables: Optional[set] = None,
    ) -> Any:
        """
        Convert Snowflake metadata to SML using SMLAssembler.
        
        Incorporates enrichments from ``_SEMANTIC_*`` tables when available
        (measures, relationships, column metadata) for a richer model.
        
        Args:
            model_id: Name for the SML model
            metadata: Full Snowflake metadata dict (may include '_semantic' key)
            include_tables: Optional set of table names to include.
                           If None, all tables are included.
        """
        from semabridge.formats.sml.assembler import SMLAssembler
        from semabridge.connectors.relationship_detector import RelationshipDetector
        from semabridge.connectors.inference_engine import SmlInferenceEngine
        from semabridge.core.settings import get_settings
        
        settings = get_settings()
        
        all_tables = metadata.get("tables", {})
        all_columns = metadata.get("columns", {})
        semantic_meta = metadata.get("_semantic", {})
        
        # Filter to selected tables, or use all
        if include_tables:
            tables = {k: v for k, v in all_tables.items() if k in include_tables}
            columns = {k: v for k, v in all_columns.items() if k in include_tables}
        else:
            tables = all_tables
            columns = all_columns
        
        assembler = SMLAssembler(
            model_name=self.config.model_name or model_id,
            description="",
            source_database=metadata.get("database", getattr(self.config.source, 'database', '') or ""),
            source_schema=metadata.get("schema", getattr(self.config.source, 'schema_name', '') or ""),
            normalize_names=False,
        )
        
        # Add only the filtered tables
        for table_name, table_info in tables.items():
            table_cols = columns.get(table_name, [])
            assembler.add_table(
                table_name=table_name,
                columns=table_cols,
                description=table_info.get("description", "") if isinstance(table_info, dict) else "",
                row_count=table_info.get("row_count") if isinstance(table_info, dict) else None,
            )
        
        # =================================================================
        # Semantic enrichment: relationships, classification, measures.
        #
        # When the source is a Semantic View, the DDL from GET_DDL is
        # authoritative — its sections are used as-is (even if empty).
        # Auto-detection is ONLY used for raw-table sources.
        # =================================================================
        sv_ddl = metadata.get("_sv_ddl", {})
        is_sv_source = bool(sv_ddl and sv_ddl.get("ddl_raw"))
        
        # Diagnostic: log the SV detection decision
        selected_svs = metadata.get("_selected_sv_names", [])
        logger.info(
            f"[SYNC DIAG] model={model_id} | "
            f"is_sv_source={is_sv_source} | "
            f"sv_ddl_keys={list(sv_ddl.keys()) if sv_ddl else 'EMPTY'} | "
            f"ddl_raw_len={len(sv_ddl.get('ddl_raw', ''))} | "
            f"selected_svs={selected_svs}"
        )
        
        # Guard: warn if SVs were selected but DDL is missing
        if selected_svs and not is_sv_source:
            logger.warning(
                f"[SYNC DIAG] SVs were selected ({selected_svs}) but "
                f"is_sv_source=False — DDL was not loaded. "
                f"This will cause auto-detection to run (measures from numeric columns)."
            )
        
        if is_sv_source:
            # ── SV path: trust DDL completely ──
            logger.info("[SV PATH] Using DDL for relationships and measures")
            
            # Relationships from DDL
            ddl_rels = sv_ddl.get("relationships", [])
            logger.info(f"[SV PATH] DDL relationships: {len(ddl_rels)}")
            for sr in ddl_rels:
                logger.info(f"  REL: {sr['from_table']}.{sr['from_column']} → {sr['to_table']}.{sr['to_column']}")
                assembler.add_relationship(
                    name=sr["name"],
                    from_table=sr["from_table"],
                    from_column=sr["from_column"],
                    to_table=sr["to_table"],
                    to_column=sr["to_column"],
                )
            
            self._deactivate_ambiguous_paths(assembler._relationships)
            
            # Measures from DDL (if none defined, that's valid — zero measures)
            ddl_measures = sv_ddl.get("measures", [])
            logger.info(f"[SV PATH] DDL measures: {len(ddl_measures)}")
            for dm in ddl_measures:
                logger.info(f"  MEASURE: {dm['name']} on {dm['table_name']} ({dm.get('aggregation','?')}({dm.get('source_column','?')}))")
                assembler.add_metric(
                    name=dm["name"],
                    dataset=dm["table_name"],
                    source_column=dm.get("source_column", ""),
                    aggregation=dm.get("aggregation", "sum"),
                    expression=dm.get("expression", ""),
                )
        else:
            # ── Raw-table path: auto-detection with fallbacks ──
            
            # --- Relationships ---
            semantic_rels = semantic_meta.get("relationships", [])
            if semantic_rels:
                logger.info(f"Using {len(semantic_rels)} pre-defined semantic relationships")
                relationships = []
                for sr in semantic_rels:
                    rel_dict = {
                        "name": sr.get("name", f"REL_{sr.get('from_table')}_{sr.get('to_table')}"),
                        "from_table": sr["from_table"],
                        "from_column": sr["from_column"],
                        "to_table": sr["to_table"],
                        "to_column": sr["to_column"],
                    }
                    assembler.add_relationship(**rel_dict)
                    relationships.append(rel_dict)
            else:
                rel_detector = RelationshipDetector(
                    tables=tables,
                    columns=columns,
                    primary_keys=metadata.get("primary_keys", {}),
                    explicit_fks=metadata.get("foreign_keys", []),
                    include_inferred=False,
                )
                relationships = rel_detector.detect_all()
                for rel in relationships:
                    assembler.add_relationship(
                        name=rel["name"],
                        from_table=rel["from_table"],
                        from_column=rel["from_column"],
                        to_table=rel["to_table"],
                        to_column=rel["to_column"],
                    )
            
            self._deactivate_ambiguous_paths(assembler._relationships)
            
            # Classify tables (FACT vs DIMENSION)
            inference = SmlInferenceEngine(
                tables=tables,
                columns=columns,
                relationships=relationships,
                primary_keys=metadata.get("primary_keys", {}),
            )
            scores = inference.classify()
            
            classification_map = {}
            for ds in assembler._datasets:
                raw_name = ds.source_table or ds.unique_name
                score = scores.get(raw_name)
                if score:
                    classification = score.classification
                    classification_map[raw_name] = classification
                    ds.is_fact = classification == "FACT"
            
            # --- Measures ---
            semantic_measures = semantic_meta.get("measures", [])
            if semantic_measures:
                logger.info(f"Using {len(semantic_measures)} pre-defined semantic measures")
                for sm in semantic_measures:
                    assembler.add_metric(
                        name=sm["name"],
                        dataset=sm.get("table_name", ""),
                        source_column=sm.get("source_column", ""),
                        aggregation=sm.get("aggregation", "sum"),
                        expression=sm.get("expression", ""),
                        description=sm.get("description", ""),
                    )
            else:
                logger.info("No semantic measures found; strict mode keeps physical columns as columns (0 auto measures).")
        
        sml_model = assembler.build()
        
        # Diagnostic summary
        logger.info(
            f"[SYNC SUMMARY] model={model_id} | "
            f"path={'SV' if is_sv_source else 'RAW'} | "
            f"datasets={len(sml_model.datasets)} | "
            f"relationships={len(sml_model.relationships)} | "
            f"metrics={len(sml_model.metrics)}"
        )
        for ds in sml_model.datasets:
            logger.info(f"  TABLE: {ds.unique_name} ({len(ds.columns)} columns)")
        for m in sml_model.metrics:
            logger.info(f"  METRIC: {m.unique_name} -> {m.dataset}.{m.source_column} ({m.aggregation})")
        
        return sml_model
    
    
    @staticmethod
    def _deactivate_ambiguous_paths(relationships: list) -> None:
        """
        Detect and deactivate relationships that create ambiguous paths.
        
        Fabric/Analysis Services requires that between any two tables
        there is at most ONE active path. When a direct relationship
        (A→B) coexists with an indirect path (A→C→B), we deactivate
        the direct one so the model can be deployed.
        
        Modifies the relationship list in-place by setting is_active=False
        on redundant edges.
        """
        from collections import defaultdict, deque
        
        if len(relationships) < 2:
            return
        
        # Build an undirected adjacency list from active relationships
        def _build_graph(exclude_idx: int = -1):
            adj = defaultdict(set)
            for i, rel in enumerate(relationships):
                if i == exclude_idx or not rel.is_active:
                    continue
                a = rel.from_dataset
                b = rel.to_dataset
                adj[a].add(b)
                adj[b].add(a)
            return adj
        
        def _reachable(adj, start, end):
            """BFS check: is 'end' reachable from 'start'?"""
            if start == end:
                return True
            visited = {start}
            queue = deque([start])
            while queue:
                node = queue.popleft()
                for neighbor in adj.get(node, set()):
                    if neighbor == end:
                        return True
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            return False
        
        deactivated = []
        for i, rel in enumerate(relationships):
            if not rel.is_active:
                continue
            # Build graph WITHOUT this relationship
            adj = _build_graph(exclude_idx=i)
            # If the two tables are still connected, this is a redundant edge
            if _reachable(adj, rel.from_dataset, rel.to_dataset):
                rel.is_active = False
                deactivated.append(f"{rel.from_dataset}→{rel.to_dataset}")
        
        if deactivated:
            logger.info(
                f"Deactivated {len(deactivated)} ambiguous relationships: "
                + ", ".join(deactivated)
            )
    
    def _convert_fabric_to_osi(self, model_id: str, metadata: Dict[str, Any]) -> Any:
        """
        Convert Fabric TMSL metadata to OSI format.
        
        This is the original conversion path for Fabric sources.
        """
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
        
        tmsl_converter = TMSLToOSIConverter()
        osi_model = tmsl_converter.to_osi({
            "tmsl": metadata,
            "dataset_id": model_id,
        })
        
        # Apply granular exclusions post-conversion (duck-typed)
        osi_model = self._apply_exclusions(osi_model)
        
        # Run Tiered Safety Pipeline directly on OSI model
        try:
            from semabridge.converter.safety_pipeline import TieredSafetyPipeline
            from pathlib import Path
            
            override_dir = Path("output/manual_sql_overrides")
            override_dir.mkdir(parents=True, exist_ok=True)
            
            safety_pipeline = TieredSafetyPipeline(override_dir=override_dir)
            safety_result = safety_pipeline.run_osi(osi_model)
            
            report = safety_result.get_report()
            logger.info(
                f"Tiered Safety for {model_id}: "
                f"T1={report['tier_distribution'][1]}, T2={report['tier_distribution'][2]}, "
                f"T3={report['tier_distribution'][3]}, T4={report['tier_distribution'][4]} | "
                f"Auto: {report['auto_translatable']}, Override: {report['override_required']}"
            )
        except Exception as e:
            logger.warning(f"Tiered Safety pipeline skipped for {model_id}: {e}")
        
        return osi_model
    
    def _validate_models(self, osi_models: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate OSI models against physical constraints.
        
        Args:
            osi_models: Dict of OSI models to validate
            
        Returns:
            Dict of valid OSI models
        """
        valid_models = {}
        for model_id, model in osi_models.items():
            # Placeholder for future validation logic
            # e.g., check for valid identifiers, circular dependencies, etc.
            valid_models[model_id] = model

        # Module 3: Structural dedup — detect models with identical
        # datasets/columns/metrics even if their display names differ.
        if hasattr(self.config, 'options') and self.config.options.structural_dedup:
            try:
                valid_models, struct_dupes = deduplicate_models(valid_models)
                if struct_dupes:
                    for g in struct_dupes:
                        logger.warning(
                            f"Structural duplicate: {g.summary()}"
                        )
            except Exception as exc:
                logger.warning(f"Structural dedup skipped: {exc}")
        else:
            logger.debug("Structural deduplication disabled via config. Skipping.")

        return valid_models

    def _apply_exclusions(self, osi_model: Any) -> Any:
        """Apply granular exclusions to an OSI model."""
        options = self.config.options
        
        # Filter tables
        if hasattr(osi_model, 'datasets'):
            osi_model.datasets = [
                ds for ds in osi_model.datasets
                if not options.is_table_excluded(ds.unique_name)
            ]
        
        # Filter columns within remaining tables
        if hasattr(osi_model, 'datasets'):
            for dataset in osi_model.datasets:
                if hasattr(dataset, 'columns'):
                    dataset.columns = [
                        col for col in dataset.columns
                        if not options.is_column_excluded(col.unique_name)
                    ]
        
        # Filter measures
        if hasattr(osi_model, 'metrics'):
            osi_model.metrics = [
                m for m in osi_model.metrics
                if not options.is_measure_excluded(m.unique_name)
            ]
        
        return osi_model
    
    def broadcast(self, sml_models: List[Any]) -> List[BroadcastResult]:
        """
        Public API for broadcasting SML models to all configured targets.
        
        This can be used for rollback operations (re-broadcasting past version).
        
        Args:
            sml_models: List of SML models to broadcast
            
        Returns:
            List of BroadcastResult
        """
        return self._broadcast_to_targets(sml_models)
    
    def discover(self, pattern: str = None) -> List[str]:
        """
        Public API for discovering models.
        
        Args:
            pattern: Optional pattern to override configuration
            
        Returns:
            List of discovered model names
        """
        if pattern:
            # Temporarily override pattern
            original_pattern = self.config.source.model
            self.config.source.model = pattern
            try:
                discovered = self._discover_models()
            finally:
                self.config.source.model = original_pattern
            return discovered
        
        return self._discover_models()

    def _broadcast_to_targets(self, sml_models: List[Any]) -> List[BroadcastResult]:
        """
        Broadcast SML models to all configured targets.
        
        Models are deployed **sequentially** within each target to prevent
        DDL races (e.g. two models both creating / dropping the same
        shared source table).  Different *targets* still run in parallel
        so multi-target latency stays at ``max(T_target_i)``.
        
        Args:
            sml_models: List of SML models to broadcast
            
        Returns:
            List of BroadcastResult for each target
        """
        results: List[BroadcastResult] = []
        targets = self.config.targets
        
        if not targets:
            logger.warning("No targets configured for broadcast")
            return results
        
        def _deploy_all_models_to_target(target: TargetConfig) -> List[BroadcastResult]:
            """Deploy every model to *one* target, sequentially.

            Opens a shared Snowflake session before the batch so all models
            reuse the same connection (P2a).
            """
            target_results: List[BroadcastResult] = []
            connector = self._get_or_create_target_connector(target)

            # Open session for batch reuse (no-op if connector doesn't support it)
            if hasattr(connector, "open_session"):
                try:
                    connector.open_session()
                except Exception as e:
                    logger.warning(f"Failed to open session for {target.type.value}: {e}")

            try:
                for sml_model in sml_models:
                    try:
                        result = self._deploy_to_target(target, sml_model)
                        target_results.append(result)
                    except Exception as e:
                        target_results.append(BroadcastResult(
                            target=target,
                            success=False,
                            message=str(e),
                            error=e,
                        ))
            finally:
                if hasattr(connector, "close_session"):
                    try:
                        connector.close_session()
                    except Exception as e:
                        logger.warning(f"Failed to close session for {target.type.value}: {e}")

            return target_results
        
        if len(targets) == 1:
            # Fast path — no thread pool needed
            results = _deploy_all_models_to_target(targets[0])
        else:
            # Parallelize across targets; models stay sequential per target
            with ThreadPoolExecutor(max_workers=min(len(targets), self.MAX_BROADCAST_WORKERS)) as executor:
                futures = {
                    executor.submit(_deploy_all_models_to_target, target): target
                    for target in targets
                }
                for future in as_completed(futures):
                    results.extend(future.result())
        
        return results
    
    def _deploy_to_target(self, target: TargetConfig, sml_model: Any) -> BroadcastResult:
        """
        Deploy an SML model to a single target with semaphore protection.
        
        Uses bulkhead isolation via semaphores to prevent overwhelming targets.
        """
        # Handle Dry Run / Skip Deployment
        if not target.deploy:
            logger.info(f"Dry run for target {target.type.value}: Skipping physical deployment")
            return BroadcastResult(
                target=target,
                success=True,
                message="Dry run (deployment disabled)",
                duration_ms=0.0,
            )

        semaphore = self._get_or_create_semaphore(target)
        
        start = time.perf_counter()
        
        # Acquire semaphore (load shedding)
        acquired = semaphore.acquire(blocking=True, timeout=30.0)
        if not acquired:
            return BroadcastResult(
                target=target,
                success=False,
                message="Timeout waiting for target semaphore (load shedding)",
            )
        
        try:
            connector = self._get_or_create_target_connector(target)
            success = connector.deploy(sml_model)
            
            duration = (time.perf_counter() - start) * 1000
            
            return BroadcastResult(
                target=target,
                success=success,
                message="Deployed successfully" if success else "Deployment returned False",
                duration_ms=duration,
            )
            
        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            logger.error(f"Deployment to {target.type.value} failed: {e}")
            return BroadcastResult(
                target=target,
                success=False,
                message=str(e),
                duration_ms=duration,
                error=e,
            )
        finally:
            semaphore.release()
    
    def _create_source_connector(self) -> SourceConnectorBase:
        """Create a source connector based on configuration."""
        from semabridge.core.project import SourceType
        
        source_type = self.config.source.type
        
        if source_type == SourceType.FABRIC:
            from semabridge.connectors.fabric_extractor import FabricExtractor
            from semabridge.core.settings import get_settings
            
            settings = get_settings()
            extractor = FabricExtractor(settings.fabric)
            
            # Wrap in adapter that implements our interface
            return FabricSourceAdapter(extractor, self.config.source)
        
        elif source_type == SourceType.SNOWFLAKE:
            from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
            from semabridge.core.settings import get_settings
            
            settings = get_settings()
            extractor = SnowflakeExtractor(settings.snowflake)
            
            return SnowflakeSourceAdapter(extractor, self.config.source)
        
        raise ValueError(f"Unsupported source type: {source_type}")
    
    def _get_or_create_target_connector(self, target: TargetConfig) -> TargetConnectorBase:
        """Get or create a target connector."""
        from semabridge.core.project import TargetType
        
        # Use string value for key (safe even if enum repr changes)
        target_type_value = target.type.value if hasattr(target.type, 'value') else str(target.type)
        key = f"{target_type_value}:{target.database}:{target.schema_name}"
        
        if key not in self.target_connectors:
            # Use string-value comparison for robustness across Python versions
            if target_type_value in ("snowflake_semantic_view", "snowflake"):
                from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
                from semabridge.core.settings import get_settings
                
                settings = get_settings()
                emitter = SnowflakeEmitter(settings.snowflake)
                self.target_connectors[key] = SnowflakeTargetAdapter(emitter)
            elif target_type_value == "fabric":
                from semabridge.connectors.fabric_publisher import FabricPublisher
                from semabridge.core.settings import get_settings
                
                settings = get_settings()
                publisher = FabricPublisher(settings.fabric)
                self.target_connectors[key] = FabricTargetAdapter(
                    publisher, settings
                )
            else:
                raise ValueError(
                    f"Unsupported target type: {target.type} "
                    f"(value={target_type_value!r}, type={type(target.type).__name__})"
                )
        
        return self.target_connectors[key]
    
    def _cleanup(self):
        """Cleanup after execution."""
        logger.debug("Engine cleanup completed")


class FabricSourceAdapter(SourceConnectorBase):
    """Adapter wrapping FabricExtractor to our interface."""
    
    def __init__(self, extractor, source_config: SourceConfig):
        self._extractor = extractor
        self._config = source_config
    
    def discover(self, pattern: str = "*") -> List[str]:
        """Discover models using Fabric REST API."""
        import fnmatch
        
        models = self._extractor.list_semantic_models()
        names = [m.get("displayName", m.get("id", "")) for m in models]
        
        # Apply pattern matching (case-insensitive)
        return [
            name for name in names
            if fnmatch.fnmatch(name.lower(), pattern.lower())
        ]
    
    def extract(self, model_id: str, exclusions: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        """Extract TMSL definition from Fabric."""
        # Resolve name to ID if needed
        resolved_id = self._extractor.resolve_model_id(model_id)
        return self._extractor.get_model_definition(resolved_id)

    def validate_permissions(self) -> List[str]:
        """Delegate to FabricExtractor."""
        return self._extractor.validate_permissions()

    @property
    def max_concurrency(self) -> int:
        return self._extractor.max_concurrency


class SnowflakeSourceAdapter(SourceConnectorBase):
    """Adapter wrapping SnowflakeExtractor to our interface."""
    
    def __init__(self, extractor, source_config: SourceConfig):
        self._extractor = extractor
        self._config = source_config
    
    def discover(self, pattern: str = "*") -> List[str]:
        """Discover semantic views in Snowflake."""
        import fnmatch
        
        # List semantic views (not raw tables)
        views = self._extractor.list_semantic_views()
        names = [v.get("displayName", v.get("id", "")) for v in views]
        
        # If no semantic views found, fall back to listing raw tables
        # (for schemas that haven't had SVs created yet)
        if not names:
            logger.info("No semantic views found, falling back to table listing")
            metadata = self._extractor.extract_all()
            names = list(metadata.get("tables", {}).keys())
        
        return [
            name for name in names
            if fnmatch.fnmatch(name.lower(), pattern.lower())
        ]
    
    def extract(self, model_id: str, exclusions: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        """Extract metadata from Snowflake for a semantic view or table.

        Pulls physical table metadata (via ``extract_all``), semantic
        layer metadata from ``_SEMANTIC_*`` tables, and — when the
        source is a Semantic View — the authoritative DDL via
        ``GET_DDL('SEMANTIC VIEW', ...)``.
        """
        metadata = self._extractor.extract_all()
        # Tag with the requested model so the converter knows which
        # semantic view / table to build the SML model around
        metadata["_requested_model"] = model_id

        # Enrich with semantic layer metadata (_SEMANTIC_MEASURES,
        # _SEMANTIC_RELATIONSHIPS, _SEMANTIC_COLUMNS) if available
        try:
            semantic_meta = self._extractor.read_semantic_tables()
            metadata["_semantic"] = semantic_meta
        except Exception as e:
            logger.debug(f"Could not read _SEMANTIC_* tables: {e}")

        # Read the Semantic View DDL (authoritative relationships & measures)
        try:
            sv_ddl = self._extractor.read_semantic_view_ddl(model_id)
            if sv_ddl and sv_ddl.get("ddl_raw"):
                metadata["_sv_ddl"] = sv_ddl
                logger.info(
                    f"[DDL OK] Read SV DDL for '{model_id}': "
                    f"{len(sv_ddl.get('relationships', []))} rels, "
                    f"{len(sv_ddl.get('measures', []))} measures, "
                    f"ddl_raw_len={len(sv_ddl.get('ddl_raw', ''))}"
                )
            else:
                logger.warning(
                    f"[DDL EMPTY] read_semantic_view_ddl returned no ddl_raw for '{model_id}'. "
                    f"sv_ddl keys={list(sv_ddl.keys()) if sv_ddl else 'None'}. "
                    f"Sync will fall back to auto-detection."
                )
        except Exception as e:
            logger.warning(
                f"[DDL FAIL] Could not read SV DDL for '{model_id}': {e}. "
                f"Sync will fall back to auto-detection."
            )

        return metadata


class SnowflakeTargetAdapter(TargetConnectorBase):
    """Adapter wrapping SnowflakeEmitter to our interface."""
    
    def __init__(self, emitter):
        self._emitter = emitter
    
    def deploy(self, model: Any) -> bool:
        """Deploy model to Snowflake as semantic view.

        Detects model type and routes to the appropriate emitter method:
        OSI models → ``deploy_from_osi``, SML models → ``deploy``.
        """
        try:
            # Check if model is an OSI model (has source_platform attr)
            model_cls = type(model).__name__
            if model_cls == "OSIModel" or hasattr(model, "source_platform"):
                self._emitter.deploy_from_osi(model)
            else:
                self._emitter.deploy(model)
            return True
        except Exception:
            raise

    def open_session(self) -> None:
        """Open a shared connection session on the underlying emitter."""
        if hasattr(self._emitter, "open_session"):
            self._emitter.open_session()

    def close_session(self) -> None:
        """Close the shared connection session."""
        if hasattr(self._emitter, "close_session"):
            self._emitter.close_session()

    def validate_permissions(self) -> List[str]:
        """Delegate to SnowflakeEmitter."""
        return self._emitter.validate_permissions()

    @property
    def max_concurrency(self) -> int:
        return self._emitter.max_concurrency


class FabricTargetAdapter(TargetConnectorBase):
    """Adapter wrapping FabricPublisher to our target interface."""

    def __init__(self, publisher, settings):
        self._publisher = publisher
        self._settings = settings

    def deploy(self, model: Any) -> bool:
        """Deploy an SML model to Microsoft Fabric.

        Generates TMSL via FabricPublisher.publish() and uploads to
        the configured workspace.
        """
        try:
            sf = self._settings.snowflake
            behavior = getattr(self._settings, 'behavior', None)
            overwrite = True
            if behavior and hasattr(behavior, 'fabric'):
                overwrite = getattr(behavior.fabric, 'deploy_overwrite', True)

            self._publisher.publish(
                sml_model=model,
                snowflake_server=sf.account,
                snowflake_warehouse=sf.warehouse,
                snowflake_database=sf.database,
                snowflake_schema=sf.schema_name,
                overwrite=overwrite,
            )
            return True
        except Exception:
            raise

    def validate_permissions(self) -> List[str]:
        """Fabric does not expose a permission-check endpoint."""
        return []

    @property
    def max_concurrency(self) -> int:
        # Fabric REST API is rate-limited; conservative default
        return 2
