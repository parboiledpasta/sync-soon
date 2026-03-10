"""
CLI Execution Engine v1.

Orchestrates the mandatory 10-step execution flow where each CLI invocation
corresponds to exactly one Project and one Run.

Execution Flow:
1. Load and Validate Configuration
2. Initialize Identifiers (project_id, run_id)
3. Resolve Authentication
4. Extract from Source
5. Validate and Parse Source Format
6. Convert to Canonical SML
7. Persist Artifacts
8. Convert to Target Format (Optional)
9. Deploy to Target (Optional)
10. Finalize Run
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from semabridge.core.settings import Settings, get_settings
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.run_summary import (
    STEP_NAMES,
    RunStatus,
    RunSummary,
    StepStatus,
    create_run_summary,
)
from semabridge.core.source_format import (
    SourceFormat,
    from_fabric_tmsl,
    from_snowflake_metadata,
)
from semabridge.formats.sml.models import SMLModel
from semabridge.repository.duckdb_manager import DuckDBManager
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""
    pass


class AuthenticationError(Exception):
    """Raised when authentication resolution fails."""
    pass


class ExtractionError(Exception):
    """Raised when source extraction fails."""
    pass


class SourceFormatError(Exception):
    """Raised when source format validation fails."""
    pass


class ConversionError(Exception):
    """Raised when SML conversion fails."""
    pass


class PersistenceError(Exception):
    """Raised when artifact persistence fails."""
    pass


class DeploymentError(Exception):
    """Raised when target deployment fails."""
    pass


@dataclass
class RunContext:
    """
    Execution context propagated through all steps.
    
    Generated at Step 2 and used throughout execution.
    """
    project_id: str
    run_id: str
    config: Settings
    start_time: float
    source_type: Literal["snowflake", "fabric"]
    target_type: Optional[Literal["snowflake", "fabric"]] = None
    behavior: ConnectorBehavior = Field(default_factory=ConnectorBehavior)
    
    # Artifacts accumulated during execution
    source_format: Optional[SourceFormat] = None
    sml_model: Optional[SMLModel] = None
    source_artifact_id: Optional[str] = None
    sml_snapshot_id: Optional[str] = None
    target_artifact_path: Optional[str] = None


class ExecutionEngine:
    """
    Orchestrates CLI execution following the mandatory 10-step flow.
    
    Each method corresponds to one step and must be called in order.
    The engine enforces this order and handles failures appropriately.
    """
    
    SUPPORTED_SOURCES = {"snowflake", "fabric"}
    SUPPORTED_TARGETS = {"snowflake", "fabric", None}
    
    def __init__(self, db_manager: Optional[DuckDBManager] = None):
        self.db_manager = db_manager or DuckDBManager()
        self._current_step = 0
        self._context: Optional[RunContext] = None
        self._summary: Optional[RunSummary] = None
    
    def execute(
        self,
        source: Literal["snowflake", "fabric"],
        target: Optional[Literal["snowflake", "fabric"]] = None,
        project_name: Optional[str] = None,
        config_path: Optional[Path] = None,
        deploy: bool = True,
        tag: Optional[str] = None,
        dry_run: bool = False,
        # Source-specific options
        dataset_id: Optional[str] = None,  # For Fabric source
        workspace_id: Optional[str] = None,  # For Fabric source
    ) -> RunSummary:
        """
        Execute the full 10-step pipeline.
        
        Args:
            source: Source connector type ("snowflake" or "fabric")
            target: Target connector type (optional)
            project_name: Override project name
            config_path: Path to config file (uses .env by default)
            deploy: Whether to deploy to target
            tag: Version tag for this run
            dry_run: Generate artifacts but don't deploy
            dataset_id: Fabric dataset ID (for Fabric source)
            workspace_id: Fabric workspace ID override
            
        Returns:
            RunSummary with execution results
        """
        try:
            # Step 1: Load and Validate Configuration
            config = self._step1_load_config(config_path, source, target)
            
            # Step 2: Initialize Identifiers
            context = self._step2_init_identifiers(
                config, source, target, project_name, dataset_id
            )
            self._context = context
            self._summary = create_run_summary(
                project_id=context.project_id,
                run_id=context.run_id,
                source_type=source,
                target_type=target,
            )
            
            # Step 3: Resolve Authentication
            self._step3_resolve_auth(context)
            
            # Step 4: Extract from Source
            source_format = self._step4_extract(
                context, dataset_id, workspace_id
            )
            context.source_format = source_format
            
            # Step 5: Validate and Parse Source Format
            self._step5_validate_source(context)
            
            # Step 6: Convert to Canonical SML
            sml_model = self._step6_convert_to_sml(context, workspace_id, dataset_id)
            context.sml_model = sml_model
            
            # Step 7: Persist Artifacts
            self._step7_persist_artifacts(context, tag)
            
            # Step 8: Convert to Target Format (Optional)
            if target and not dry_run:
                self._step8_convert_to_target(context)
            else:
                self._record_step(8, StepStatus.SKIPPED, "No target or dry run")
            
            # Step 9: Deploy to Target (Optional)
            if deploy and target and not dry_run:
                self._step9_deploy(context)
            else:
                self._record_step(9, StepStatus.SKIPPED, "Deployment skipped")
            
            # Step 10: Finalize Run
            return self._step10_finalize(context, RunStatus.SUCCESS)
            
        except Exception as e:
            logger.error(f"Execution failed at step {self._current_step}: {e}")
            
            # Determine status based on progress
            if self._current_step >= 9 and self._summary and any(
                s.status == StepStatus.SUCCESS for s in self._summary.steps_completed
                if s.step_number == 9
            ):
                status = RunStatus.PARTIAL
            else:
                status = RunStatus.FAILED
            
            if self._summary:
                self._summary.add_error(
                    self._current_step,
                    STEP_NAMES.get(self._current_step, "Unknown"),
                    e,
                    include_traceback=True,
                )
            
            return self._step10_finalize(
                self._context or self._create_fallback_context(),
                status
            )
    
    def _record_step(
        self,
        step_number: int,
        status: StepStatus,
        message: Optional[str] = None,
        artifact_ids: Optional[list] = None,
    ) -> None:
        """Record step result in summary."""
        self._current_step = step_number
        if self._summary:
            self._summary.add_step(
                step_number=step_number,
                step_name=STEP_NAMES.get(step_number, f"Step {step_number}"),
                status=status,
                message=message,
                artifact_ids=artifact_ids,
            )
    
    def _create_fallback_context(self) -> RunContext:
        """Create a fallback context for error handling."""
        return RunContext(
            project_id="unknown",
            run_id=str(uuid.uuid4()),
            config=get_settings(),
            start_time=time.time(),
            source_type="snowflake",
            behavior=ConnectorBehavior(),
        )
    
    # =========================================================================
    # Step 1: Load and Validate Configuration
    # =========================================================================
    
    def _step1_load_config(
        self,
        config_path: Optional[Path],
        source: str,
        target: Optional[str],
    ) -> Settings:
        """
        Step 1: Load and validate configuration.
        
        - Load YAML/env configuration
        - Validate required keys
        - Validate supported connector types
        - Fail fast if validation fails
        """
        self._current_step = 1
        logger.info("Step 1: Loading and validating configuration")
        
        try:
            # Load settings (from .env by default)
            config = get_settings()
            
            # Validate connector types
            if source not in self.SUPPORTED_SOURCES:
                raise ConfigValidationError(
                    f"Unsupported source connector: '{source}'. "
                    f"Supported: {self.SUPPORTED_SOURCES}"
                )
            
            if target not in self.SUPPORTED_TARGETS:
                raise ConfigValidationError(
                    f"Unsupported target connector: '{target}'. "
                    f"Supported: {self.SUPPORTED_TARGETS - {None}}"
                )
            
            self._record_step(1, StepStatus.SUCCESS, "Configuration validated")
            return config
            
        except Exception as e:
            self._record_step(1, StepStatus.FAILED, str(e))
            raise ConfigValidationError(f"Configuration validation failed: {e}") from e
    
    # =========================================================================
    # Step 2: Initialize Identifiers
    # =========================================================================
    
    def _step2_init_identifiers(
        self,
        config: Settings,
        source: str,
        target: Optional[str],
        project_name: Optional[str],
        dataset_id: Optional[str],
    ) -> RunContext:
        """
        Step 2: Initialize identifiers.
        
        - Generate unique project_id
        - Generate unique run_id
        - Create at the very start and propagate through all stages
        """
        self._current_step = 2
        logger.info("Step 2: Initializing identifiers")
        
        # Determine project_id
        if dataset_id:
            # For Fabric source, use dataset_id as project_id
            project_id = dataset_id
        elif project_name:
            project_id = project_name
        else:
            project_id = config.model.name
        
        # Behavior loading logic (matches ExecutionConfig)
        behavior = ConnectorBehavior()
        if hasattr(config, "behavior"):
            behavior = config.behavior
        elif config_path:
             # Try to load if config_path was provided and contains a policy_path
             # Note: This is imperfect as ExecutionEngine doesn't parse YAML directly here
             # relying on CLIExecutor or Settings to handle it.
             pass

        # Generate unique run_id
        run_id = str(uuid.uuid4())
        
        context = RunContext(
            project_id=project_id,
            run_id=run_id,
            config=config,
            start_time=time.time(),
            source_type=source,
            target_type=target,
            behavior=behavior,
        )
        
        logger.info(f"Identifiers: project_id={project_id}, run_id={run_id}")
        self._record_step(2, StepStatus.SUCCESS, f"run_id={run_id[:8]}...")
        
        return context
    
    # =========================================================================
    # Step 3: Resolve Authentication
    # =========================================================================
    
    def _step3_resolve_auth(self, context: RunContext) -> None:
        """
        Step 3: Resolve authentication.
        
        - Resolve credentials from environment variables
        - Validate all required variables present
        - Do not allow inline secrets in config files
        """
        self._current_step = 3
        logger.info("Step 3: Resolving authentication")
        
        config = context.config
        missing = []
        
        # Validate source auth
        if context.source_type == "snowflake":
            if not config.validate_snowflake():
                missing.append("Snowflake credentials (SNOWFLAKE_*)")
        elif context.source_type == "fabric":
            if not config.validate_fabric():
                missing.append("Fabric credentials (FABRIC_*)")
        
        # Validate target auth
        if context.target_type == "snowflake":
            if not config.validate_snowflake():
                missing.append("Snowflake credentials (SNOWFLAKE_*)")
        elif context.target_type == "fabric":
            if not config.validate_fabric():
                missing.append("Fabric credentials (FABRIC_*)")
        
        if missing:
            msg = f"Missing authentication: {', '.join(missing)}"
            self._record_step(3, StepStatus.FAILED, msg)
            raise AuthenticationError(msg)
        
        self._record_step(3, StepStatus.SUCCESS, "Authentication resolved from ENV")
    
    # =========================================================================
    # Step 4: Extract from Source
    # =========================================================================
    
    def _step4_extract(
        self,
        context: RunContext,
        dataset_id: Optional[str],
        workspace_id: Optional[str],
    ) -> SourceFormat:
        """
        Step 4: Extract from source.
        
        - Connect to configured source system
        - Extract semantic model
        - Normalize into Source Format artifact
        """
        self._current_step = 4
        logger.info(f"Step 4: Extracting from {context.source_type}")
        
        try:
            if context.source_type == "snowflake":
                return self._extract_snowflake(context)
            elif context.source_type == "fabric":
                return self._extract_fabric(context, dataset_id, workspace_id)
            else:
                raise ExtractionError(f"Unknown source type: {context.source_type}")
                
        except Exception as e:
            self._record_step(4, StepStatus.FAILED, str(e))
            raise ExtractionError(f"Extraction failed: {e}") from e
    
    def _extract_snowflake(self, context: RunContext) -> SourceFormat:
        """Extract from Snowflake."""
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.utils.cache import MetadataCache
        
        config = context.config
        cache = MetadataCache(config.model.cache_dir) if config.model.cache_enabled else None
        
        extractor = SnowflakeExtractor(
            config=config.snowflake,
            cache=cache,
            exclude_tables=config.model.excluded_table_list,
            include_tables=config.model.included_table_list,
        )
        
        metadata = extractor.extract_all()
        semantic_data = extractor.read_semantic_tables()
        metadata["semantic_tables"] = semantic_data
        
        source_format = from_snowflake_metadata(
            project_id=context.project_id,
            run_id=context.run_id,
            metadata=metadata,
        )
        
        table_count = len(source_format.tables)
        self._record_step(4, StepStatus.SUCCESS, f"Extracted {table_count} tables")
        
        return source_format
    
    def _extract_fabric(
        self,
        context: RunContext,
        dataset_id: Optional[str],
        workspace_id: Optional[str],
    ) -> SourceFormat:
        """Extract from Fabric."""
        from semabridge.connectors.fabric_extractor import FabricExtractor
        
        config = context.config
        ws_id = workspace_id or config.fabric.workspace_id
        
        if not dataset_id:
            raise ExtractionError("dataset_id is required for Fabric source")
        
        extractor = FabricExtractor(config.fabric)
        tmsl = extractor.get_model_definition(dataset_id)
        row_counts = extractor.get_table_row_counts(dataset_id)
        
        source_format = from_fabric_tmsl(
            project_id=context.project_id,
            run_id=context.run_id,
            tmsl=tmsl,
            workspace_id=ws_id,
            dataset_id=dataset_id,
            row_counts=row_counts,
        )
        
        self._record_step(4, StepStatus.SUCCESS, f"Extracted TMSL definition")
        
        return source_format
    
    # =========================================================================
    # Step 5: Validate and Parse Source Format
    # =========================================================================
    
    def _step5_validate_source(self, context: RunContext) -> None:
        """
        Step 5: Validate and parse source format.
        
        - Apply format definition validation
        - Parse using explicit parsing instructions
        - Fail with actionable diagnostics if invalid
        """
        self._current_step = 5
        logger.info("Step 5: Validating source format")
        
        if not context.source_format:
            self._record_step(5, StepStatus.FAILED, "No source format to validate")
            raise SourceFormatError("No source format artifact available")
        
        issues = context.source_format.validate_format()
        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        
        if errors:
            msg = context.source_format.get_diagnostic_message()
            self._record_step(5, StepStatus.FAILED, f"{len(errors)} validation errors")
            raise SourceFormatError(msg)
        
        if warnings:
            self._record_step(5, StepStatus.SUCCESS, f"{len(warnings)} warnings")
        else:
            self._record_step(5, StepStatus.SUCCESS, "Source format valid")
    
    # =========================================================================
    # Step 6: Convert to Canonical SML
    # =========================================================================
    
    def _step6_convert_to_sml(
        self,
        context: RunContext,
        workspace_id: Optional[str],
        dataset_id: Optional[str],
    ) -> SMLModel:
        """
        Step 6: Convert to canonical SML.
        
        - Map validated Source Format into canonical SML
        - Ensure schema correctness and semantic consistency
        """
        self._current_step = 6
        logger.info("Step 6: Converting to canonical SML")
        
        try:
            if context.source_type == "snowflake":
                return self._convert_snowflake_to_sml(context)
            elif context.source_type == "fabric":
                return self._convert_fabric_to_sml(context, workspace_id, dataset_id)
            else:
                raise ConversionError(f"Unknown source type: {context.source_type}")
                
        except Exception as e:
            self._record_step(6, StepStatus.FAILED, str(e))
            raise ConversionError(f"SML conversion failed: {e}") from e
    
    def _convert_snowflake_to_sml(self, context: RunContext) -> SMLModel:
        """Convert Snowflake source to SML."""
        from semabridge.connectors.inference_engine import SmlInferenceEngine
        from semabridge.connectors.relationship_detector import RelationshipDetector
        from semabridge.formats.sml.assembler import SMLAssembler
        
        config = context.config
        sf = context.source_format
        
        # Convert source format back to metadata dict for existing assembler
        metadata = {
            "database": sf.database,
            "schema": sf.schema_name,
            "tables": {name: {"description": t.description, "row_count": t.row_count}
                      for name, t in sf.tables.items()},
            "columns": {name: [c.model_dump() for c in cols]
                       for name, cols in sf.columns.items()},
            "foreign_keys": [fk.model_dump() for fk in sf.foreign_keys],
            "primary_keys": sf.primary_keys,
        }
        
        assembler = SMLAssembler(
            model_name=context.project_id,
            description=config.model.description,
            source_database=sf.database,
            source_schema=sf.schema_name,
            normalize_names=False,
        )
        
        # Add tables
        for table_name, table_info in metadata["tables"].items():
            columns = metadata["columns"].get(table_name, [])
            assembler.add_table(
                table_name=table_name,
                columns=columns,
                description=table_info.get("description", ""),
                row_count=table_info.get("row_count"),
            )
        
        # Detect relationships
        rel_detector = RelationshipDetector(
            tables=metadata["tables"],
            columns=metadata["columns"],
            primary_keys=metadata["primary_keys"],
            explicit_fks=metadata["foreign_keys"],
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
        
        # Classify tables
        engine = SmlInferenceEngine(
            tables=metadata["tables"],
            columns=metadata["columns"],
            relationships=relationships,
            primary_keys=metadata["primary_keys"],
        )
        scores = engine.classify()
        
        classification_map = {}
        for ds in assembler._datasets:
            score = scores.get(ds.unique_name)
            if score:
                classification_map[ds.unique_name] = score.classification
                ds.is_fact = score.classification == "FACT"
        
        # Strict mode for Snowflake->Fabric: no heuristic measure generation
        
        sml_model = assembler.build()
        
        self._record_step(
            6, StepStatus.SUCCESS,
            f"{sml_model.dataset_count} datasets, {sml_model.metric_count} metrics"
        )
        
        return sml_model
    
    def _convert_fabric_to_sml(
        self,
        context: RunContext,
        workspace_id: Optional[str],
        dataset_id: Optional[str],
    ) -> SMLModel:
        """Convert Fabric TMSL to SML."""
        from semabridge.converter.tmsl_to_sml import TMSLTransformer
        
        sf = context.source_format
        ws_id = workspace_id or sf.workspace_id
        ds_id = dataset_id or sf.dataset_id
        
        transformer = TMSLTransformer()
        sml_model = transformer.transform(
            sf.tmsl_definition,
            ws_id,
            ds_id,
            row_counts=sf.row_counts,
            behavior=context.behavior,
        )
        
        self._record_step(
            6, StepStatus.SUCCESS,
            f"{sml_model.dataset_count} datasets, {sml_model.metric_count} metrics"
        )
        
        return sml_model
    
    # =========================================================================
    # Step 7: Persist Artifacts
    # =========================================================================
    
    def _step7_persist_artifacts(
        self,
        context: RunContext,
        tag: Optional[str],
    ) -> None:
        """
        Step 7: Persist artifacts.
        
        - Persist Source Format artifact
        - Persist Canonical SML artifact
        - Persist validation/conversion reports
        - Persist execution metadata (project_id, run_id, timestamps)
        """
        self._current_step = 7
        logger.info("Step 7: Persisting artifacts")
        
        try:
            config = context.config
            
            # Ensure project exists
            self.db_manager.ensure_project(
                project_id=context.project_id,
                name=context.sml_model.label if context.sml_model else context.project_id,
                workspace_id=config.fabric.workspace_id if context.source_type == "fabric" else "",
                adapter=context.source_type,
            )
            
            # Commit SML to DuckDB
            sml_dict = context.sml_model.model_dump(mode='json') if context.sml_model else {}
            
            committed, snapshot_id = self.db_manager.commit_model(
                project_id=context.project_id,
                sml_json=sml_dict,
                tag=tag,
                status="success",
                duration_ms=int((time.time() - context.start_time) * 1000),
                run_id=context.run_id,
            )
            
            context.sml_snapshot_id = snapshot_id
            
            # Persist source artifact
            source_artifact_id = self.db_manager.persist_source_artifact(
                run_id=context.run_id,
                source_format=context.source_format,
            )
            context.source_artifact_id = source_artifact_id
            
            if committed:
                msg = f"Snapshot {snapshot_id[:8]}... committed"
            else:
                msg = "No changes detected"
            
            self._record_step(
                7, StepStatus.SUCCESS, msg,
                artifact_ids=[snapshot_id, source_artifact_id] if source_artifact_id else [snapshot_id]
            )
            
        except Exception as e:
            self._record_step(7, StepStatus.FAILED, str(e))
            raise PersistenceError(f"Artifact persistence failed: {e}") from e
    
    # =========================================================================
    # Step 8: Convert to Target Format (Optional)
    # =========================================================================
    
    def _step8_convert_to_target(self, context: RunContext) -> None:
        """
        Step 8: Convert to target format.
        
        - Convert SML into Target Format using target rule pack
        - Validate generated output before deployment
        """
        self._current_step = 8
        logger.info(f"Step 8: Converting to {context.target_type} target format")
        
        try:
            if context.target_type == "fabric":
                self._convert_to_fabric_target(context)
            elif context.target_type == "snowflake":
                self._convert_to_snowflake_target(context)
            
            self._record_step(8, StepStatus.SUCCESS, f"Target format generated")
            
        except Exception as e:
            self._record_step(8, StepStatus.FAILED, str(e))
            raise ConversionError(f"Target conversion failed: {e}") from e
    
    def _convert_to_fabric_target(self, context: RunContext) -> None:
        """Generate Fabric TMSL."""
        from semabridge.connectors.tmsl_generator import TMSLGenerator
        
        config = context.config
        
        generator = TMSLGenerator(
            context.sml_model,
            snowflake_server=config.snowflake.account,
            snowflake_warehouse=config.snowflake.warehouse,
            snowflake_database=config.snowflake.database,
            snowflake_schema=config.snowflake.schema_name,
        )
        
        output_dir = Path("output")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        bim_path = output_dir / "model.bim"
        generator.save(bim_path)
        context.target_artifact_path = str(bim_path)
    
    def _convert_to_snowflake_target(self, context: RunContext) -> None:
        """Generate Snowflake DDL."""
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        
        config = context.config
        emitter = SnowflakeEmitter(config.snowflake, behavior=context.behavior)
        
        output_dir = Path("output/reverse")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        ddls = emitter.generate_ddls(context.sml_model)
        full_ddl = "\n\n".join(ddls)
        yaml_out = emitter.generate_cortex_yaml(context.sml_model)
        
        ddl_path = output_dir / "semantic_view.sql"
        yaml_path = output_dir / "cortex_analyst.yaml"
        
        with open(ddl_path, "w") as f:
            f.write(full_ddl)
        with open(yaml_path, "w") as f:
            f.write(yaml_out)
        
        context.target_artifact_path = str(ddl_path)
    
    # =========================================================================
    # Step 9: Deploy to Target (Optional)
    # =========================================================================
    
    def _step9_deploy(self, context: RunContext) -> None:
        """
        Step 9: Deploy to target.
        
        - Emit/deploy Target Format to target system
        - Handle partial deployment failures explicitly
        """
        self._current_step = 9
        logger.info(f"Step 9: Deploying to {context.target_type}")
        
        try:
            if context.target_type == "fabric":
                self._deploy_to_fabric(context)
            elif context.target_type == "snowflake":
                self._deploy_to_snowflake(context)
            
            self._record_step(9, StepStatus.SUCCESS, "Deployment complete")
            
        except Exception as e:
            self._record_step(9, StepStatus.FAILED, str(e))
            raise DeploymentError(f"Deployment failed: {e}") from e
    
    def _deploy_to_fabric(self, context: RunContext) -> None:
        """Deploy to Fabric."""
        from semabridge.connectors.fabric_publisher import FabricPublisher
        
        config = context.config
        publisher = FabricPublisher(config.fabric)
        
        publisher.publish(
            sml_model=context.sml_model,
            model_name=context.project_id,
            snowflake_server=config.snowflake.account,
            snowflake_warehouse=config.snowflake.warehouse,
            snowflake_database=config.snowflake.database,
            snowflake_schema=config.snowflake.schema_name,
            overwrite=True,
        )
    
    def _deploy_to_snowflake(self, context: RunContext) -> None:
        """Deploy to Snowflake."""
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        
        config = context.config
        emitter = SnowflakeEmitter(config.snowflake)
        emitter.deploy(context.sml_model)
        
        # Optional: Sync measures from Fabric to Snowflake MEASURES_ tables
        # This materializes complex DAX measures that cannot be translated to SQL
        if context.source_type == "fabric" and self._should_sync_measures(context):
            self._sync_fabric_measures(context, emitter)
    
    # =========================================================================
    # Step 10: Finalize Run
    # =========================================================================
    
    def _step10_finalize(
        self,
        context: RunContext,
        status: RunStatus,
    ) -> RunSummary:
        """
        Step 10: Finalize run.
        
        - Record final run status (SUCCESS, FAILED, PARTIAL)
        - Produce structured run summary
        - Output to CLI
        """
        self._current_step = 10
        logger.info(f"Step 10: Finalizing run with status {status.value}")
        
        if not self._summary:
            self._summary = create_run_summary(
                project_id=context.project_id,
                run_id=context.run_id,
                source_type=context.source_type,
                target_type=context.target_type,
            )
        
        self._summary.status = status
        self._summary.source_artifact_id = context.source_artifact_id
        self._summary.sml_snapshot_id = context.sml_snapshot_id
        self._summary.target_artifact_path = context.target_artifact_path
        
        self._record_step(10, StepStatus.SUCCESS, f"Status: {status.value}")
        
        return self._summary.finalize()
    
    # =========================================================================
    # Measure Sync (Complex DAX Support)
    # =========================================================================
    
    def _should_sync_measures(self, context: RunContext) -> bool:
        """
        Determine if measure sync should be performed.
        
        Checks configuration and model for syncable measures.
        """
        # Check if any measures need sync (those that couldn't be translated)
        if not context.sml_model:
            return False
        
        # Check for measures that are sync-enabled but lack SQL expression
        for metric in context.sml_model.metrics:
            if metric.sync_enabled and not metric.sql_expression:
                return True
        
        return False
    
    def _sync_fabric_measures(self, context: RunContext, emitter) -> None:
        """
        Sync Fabric measures to Snowflake MEASURES_ tables.
        
        Evaluates DAX measures and writes results to Snowflake.
        """
        from semabridge.connectors.fabric_extractor import FabricExtractor
        
        config = context.config
        
        # Initialize Fabric extractor
        fabric_extractor = FabricExtractor(config.fabric)
        
        # Get dataset ID from source format metadata
        dataset_id = None
        if context.source_format:
            dataset_id = getattr(context.source_format, 'model_id', None)
        
        if not dataset_id:
            logger.warning("Cannot sync measures: no dataset ID available")
            return
        
        # Sync all measures
        try:
            results = emitter.sync_all_measures(
                sml=context.sml_model,
                fabric_extractor=fabric_extractor,
                dataset_id=dataset_id,
            )
            
            success_count = sum(1 for r in results.values() if r.get("status") == "success")
            logger.info(f"Measure sync complete: {success_count}/{len(results)} measures synced")
            
        except Exception as e:
            logger.error(f"Measure sync failed: {e}")
            # Don't fail the deployment, just log warning
            logger.warning("Continuing despite measure sync failure")

