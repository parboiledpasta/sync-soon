"""
Semabridge CLI.

Main entry point for the Semabridge pipeline.

Commands:
- extract: Extract Snowflake metadata
- build: Build SML model from metadata  
- emit: Generate model.bim from SML
- publish: Deploy to Fabric
- sync: Full pipeline (extract → build → emit → publish)
- config: Show configuration
- validate: Validate connections
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

# Add the parent directory to sys.path to allow 'from semabridge import ...'
# when running directly from the project root.
root_dir = Path(__file__).resolve().parent
if root_dir.name == "semabridge" and str(root_dir.parent) not in sys.path:
    sys.path.insert(0, str(root_dir.parent))

import json
import re
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from dotenv import load_dotenv
load_dotenv()

from semabridge import __version__
from semabridge.core.settings import get_settings, reload_settings
from semabridge.utils.logger import setup_logging, get_logger

# Import reverse flow components (lazy loaded in command but good to have ready)
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.converter.tmsl_to_sml import TMSLTransformer
from semabridge.repository.duckdb_manager import DuckDBManager
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

# Import semantic CLI commands
from semabridge.cli.semantic_commands import semantic_app
from semabridge.cli.diff_commands import diff_app
from semabridge.cli.logs_commands import logs_app
from semabridge.cli.sentinel_cli import sentinel_app

# Initialize Typer app with ASCII-safe help text for Windows console compatibility
app = typer.Typer(
    name="semabridge",
    help="Semabridge - Snowflake to Fabric Semantic Model Pipeline",
    add_completion=False,
)

# Register sub-apps
from semabridge.cli.uv_commands_cli import uv_app
app.add_typer(uv_app, name="uv", help="UV Build System - Unified project commands")
app.add_typer(semantic_app, name="semantic", help="Semantic API commands")
app.add_typer(diff_app, name="diff", help="Compare semantic models")
app.add_typer(logs_app, name="logs", help="View command execution logs")
app.add_typer(sentinel_app, name="sentinel", help="Monitor and fix compilation errors")

# Force UTF-8 encoding for console output on Windows
if sys.platform == "win32":
    try:
        # Use reconfigure if available (Python 3.7+)
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

console = Console(force_terminal=True, no_color=False)
logger = get_logger(__name__)


# Simple ASCII banner (Windows console compatible)
BANNER = """
+---------------------------------------------------------------+
|    ____                       ____       _     _              |
|   / ___|  ___ _ __ ___   __ _| __ ) _ __(_) __| | __ _  ___   |
|   \\___ \\ / _ \\ '_ ` _ \\ / _` |  _ \\| '__| |/ _` |/ _` |/ _ \\  |
|    ___) |  __/ | | | | | (_| | |_) | |  | | (_| | (_| |  __/  |
|   |____/ \\___|_| |_| |_|\\__,_|____/|_|  |_|\\__,_|\\__, |\\___|  |
|                                                  |___/        |
|                                                               |
+---------------------------------------------------------------+
"""


def show_banner():
    """Display the application banner."""
    try:
        # Try Rich console first
        console.print(BANNER, style="cyan")
    except (UnicodeEncodeError, UnicodeDecodeError, OSError) as e:
        # Fallback for Windows consoles with encoding issues
        try:
            print("=== SemaBridge - Snowflake to Fabric Pipeline ===")
        except Exception:
            pass  # Silently fail if even simple print doesn't work


def safe_print(*args, **kwargs):
    """Safe print wrapper that handles Windows encoding issues."""
    try:
        console.print(*args, **kwargs)
    except (UnicodeEncodeError, UnicodeDecodeError, OSError):
        try:
            # Convert to plain text and print
            plain_text = " ".join(str(arg) for arg in args)
            print(plain_text)
        except Exception:
            pass


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
    log_level: Optional[str] = typer.Option(
        None, 
        "--log-level", 
        help="Override logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL). Takes precedence over YAML config."
    ),
    ui: bool = typer.Option(
        False,
        "--ui", "--UI",
        help="Launch the SemaBridge desktop UI instead of CLI mode."
    ),
):
    """Semabridge - Automate Fabric semantic model generation from Snowflake."""
    
    # Launch UI if requested
    if ui:
        try:
            # Set High DPI policy globally before any Qt imports/instantiation
            try:
                from PyQt6.QtGui import QGuiApplication
                from PyQt6.QtCore import Qt
                QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
                )
            except ImportError:
                pass  # Dependency check below will handle this
                
            from semabridge.cli.ui.main_window import launch_ui
            console.print("[cyan]Launching SemaBridge UI...[/cyan]")
            raise typer.Exit(launch_ui())
        except ImportError as e:
            console.print(f"[red]Error:[/red] PyQt6 is required for --ui mode.\n"
                          f"Install with: pip install PyQt6\n"
                          f"Details: {e}")
            raise typer.Exit(1)
    
    # Resolve log level
    settings = get_settings()
    yaml_level = settings.logging.level if settings.logging else "INFO"
    yaml_log_dir = settings.logging.dir if settings.logging else None
    
    effective_level = log_level or ("DEBUG" if verbose else yaml_level)
    
    # Initialize logging
    setup_logging(level=effective_level, log_dir=yaml_log_dir)
    
    # Log override if applicable
    if log_level:
        logger.debug(f"CLI log level provided ({log_level}); overriding project config ({yaml_level})")
    elif verbose:
        logger.debug(f"Verbose flag provided; setting log level to DEBUG (overriding {yaml_level})")

    # ---- Global config change detection (Requirement #4) -----------------
    # Skip the check for 'init' (bootstrapping) and 'version' (trivial).
    invoked_command = ctx.invoked_subcommand
    if invoked_command not in ("init", "version", None):
        try:
            from semabridge.core.global_settings import GlobalConfigManager
            gcm = GlobalConfigManager()
            if gcm.path.exists() and gcm.has_changed():
                console.print()
                console.print(
                    "[yellow][!] Global configuration (config.yaml) has been "
                    "modified since it was last applied.[/yellow]"
                )
                apply = typer.confirm(
                    "Do you wish to apply the updated global configuration before proceeding?",
                    default=True,
                )
                if apply:
                    errors = gcm.validate()
                    if errors:
                        console.print("[red]Global config validation failed:[/red]")
                        for err in errors:
                            console.print(f"  - {err}")
                        raise typer.Exit(code=1)
                    gcm.acknowledge()
                    console.print("[green][OK] Global configuration applied.[/green]")
                    # Force reload settings so new values take effect
                    reload_settings()
                else:
                    console.print("[dim]Proceeding with previously applied configuration.[/dim]")
        except ImportError:
            pass  # GlobalConfigManager not available (shouldn't happen)
        except typer.Exit:
            raise
        except Exception as exc:
            logger.debug(f"Global config change check skipped: {exc}")


@app.command()
def version():
    """Show version information."""
    console.print(f"Semabridge v{__version__}")


@app.command()
def init(
    local: bool = typer.Option(
        False, "--local", "-l",
        help="Create repository in current working directory"
    ),
    path: Optional[Path] = typer.Option(
        None, "--path", "-p",
        help="Custom repository path (default: ~/.semabridge/semabridge.db)"
    ),
    log_dir: Optional[str] = typer.Option(
        None, "--log-dir",
        help="Custom directory for log files"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip interactive prompts and use defaults"
    ),
):
    """
    Initialize Semabridge repository and global configuration.
    
    Creates a DuckDB repository for version control and a global
    config.yaml with default settings. Existing databases are
    migrated to the latest schema without data loss.
    
    Note: config.yaml (global) is a SEPARATE entity from semabridge.yaml (project).
    By default, config.yaml is stored alongside the DuckDB repository.
    
    Examples:
        semabridge init                  # Interactive mode
        semabridge init --local          # Create in current directory
        semabridge init --path ./myrepo  # Custom path
        semabridge init --yes            # Non-interactive with defaults
    """
    from semabridge.core.initializer import SemabridgeInitializer
    from rich.progress import Progress, SpinnerColumn, TextColumn
    
    show_banner()
    
    # --------------- Collect settings from user (interactive) ---------------
    # These will be passed to the initializer to customize config.yaml.
    llm_enabled: bool = False
    llm_model: str = "gpt-4o-mini"
    intermediate_format: str = "OSI"
    max_concurrent_models: int = 4
    thread_pool_size: int = 8
    
    # Determine repository path
    if local:
        repository_path = None  # Initializer will use cwd
        console.print(Panel.fit(
            f"[bold]Initialize Semabridge (Local Mode)[/bold]\n"
            f"Repository: [cyan]{Path.cwd() / 'semabridge.db'}[/cyan]",
            title="Init",
        ))
    elif path:
        repository_path = path
        console.print(Panel.fit(
            f"[bold]Initialize Semabridge[/bold]\n"
            f"Repository: [cyan]{path}[/cyan]",
            title="Init",
        ))
    elif not yes:
        # Interactive mode: prompt for path
        default_path = SemabridgeInitializer.get_default_repository_path()
        console.print(Panel.fit(
            f"[bold]Initialize Semabridge[/bold]\n"
            f"This will create a DuckDB repository and global configuration (config.yaml).",
            title="Init",
        ))
        console.print()
        console.print(f"[dim]Default location: {default_path}[/dim]")
        user_input = typer.prompt(
            "Where would you like to store the Semabridge repository?",
            default=str(default_path),
            show_default=False
        )
        repository_path = Path(user_input) if user_input != str(default_path) else None
        
        # Interactive mode: prompt for log dir
        default_log_dir = "./logs" if local else "~/.semabridge/logs"
        user_log = typer.prompt(
            "Where would you like to store logs?",
            default=default_log_dir,
            show_default=True
        )
        if user_log != default_log_dir:
            log_dir = user_log

        # Interactive: intermediate format (OSI / SML)
        console.print()
        console.print("[bold]Intermediate format preference[/bold]")
        console.print("[dim]OSI (Open Semantic Intermediate) or SML (Semantic Modeling Language)[/dim]")
        fmt_choice = typer.prompt(
            "Intermediate format",
            default="OSI",
            show_default=True,
        ).upper()
        if fmt_choice in ("OSI", "SML"):
            intermediate_format = fmt_choice
        else:
            console.print(f"[yellow]Invalid choice '{fmt_choice}'. Defaulting to OSI.[/yellow]")

        # Interactive: LLM settings
        console.print()
        console.print("[bold]LLM support[/bold] (for Tier 4 DAX-to-SQL conversion)")
        llm_enabled = typer.confirm("Enable LLM support?", default=False)
        if llm_enabled:
            llm_model = typer.prompt(
                "Preferred AI model",
                default="gpt-4o-mini",
                show_default=True,
            )

        # Interactive: concurrency
        console.print()
        console.print("[bold]Concurrency settings[/bold]")
        try:
            max_concurrent_models = int(typer.prompt(
                "Max concurrent models",
                default="4",
                show_default=True,
            ))
        except ValueError:
            max_concurrent_models = 4
        try:
            thread_pool_size = int(typer.prompt(
                "Thread pool size",
                default="8",
                show_default=True,
            ))
        except ValueError:
            thread_pool_size = 8

        console.print()
        console.print("[dim]Note: config.yaml will be stored alongside the DuckDB repository.[/dim]")
            
    else:
        # Non-interactive mode with defaults
        repository_path = None
        default_path = SemabridgeInitializer.get_default_repository_path()
        console.print(Panel.fit(
            f"[bold]Initialize Semabridge[/bold]\n"
            f"Repository: [cyan]{default_path}[/cyan]",
            title="Init",
        ))
    
    console.print()
    
    # Perform initialization with progress
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(description="Initializing repository...", total=None)
        
        initializer = SemabridgeInitializer(
            repository_path=repository_path,
            local=local,
            log_dir=log_dir,
        )
        result = initializer.initialize()
    
    # Post-init: patch config.yaml with interactive choices
    if result.success and result.config_path and result.config_path.exists():
        try:
            import yaml as _yaml
            with open(result.config_path, "r", encoding="utf-8") as f:
                cfg = _yaml.safe_load(f) or {}
            
            # Apply user choices
            cfg.setdefault("core", {})["intermediate_format"] = intermediate_format
            cfg.setdefault("llm", {})["enabled"] = llm_enabled
            cfg.setdefault("llm", {})["model"] = llm_model
            cfg.setdefault("concurrency", {})["max_concurrent_models"] = max_concurrent_models
            cfg.setdefault("concurrency", {})["thread_pool_size"] = thread_pool_size
            
            with open(result.config_path, "w", encoding="utf-8") as f:
                _yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
        except Exception as exc:
            logger.debug(f"Could not patch config.yaml with interactive choices: {exc}")

        # Acknowledge the config so the first subsequent command doesn't re-prompt
        try:
            from semabridge.core.global_settings import GlobalConfigManager
            gcm = GlobalConfigManager(config_path=result.config_path)
            gcm.load()
            gcm.acknowledge()
        except Exception as exc:
            logger.debug(f"Could not acknowledge initial config: {exc}")
    
    # Display result
    if result.success:
        console.print(f"[green][OK][/green] {result.message}")
        console.print()
        
        # Show created paths
        table = Table(show_header=False, box=None)
        table.add_column("Item", style="cyan")
        table.add_column("Path", style="white")
        
        table.add_row("Repository", str(result.repository_path))
        if result.config_path:
            table.add_row("Global Config", str(result.config_path))
        
        console.print(table)
        console.print()

        console.print(f"  • Review configuration: [cyan]semabridge config[/cyan]")
        console.print(f"  • Validate configs:     [cyan]semabridge validate[/cyan]")
        console.print(f"  • Start syncing:        [cyan]semabridge sync --help[/cyan]")
        console.print(f"  • View logs:            [cyan]semabridge logs list[/cyan]")
        
        if result.config_path:
            console.print()
            console.print(
                f"[dim]Edit {result.config_path} to customize global settings.\n"
                f"This is separate from your project-level semabridge.yaml.[/dim]"
            )
    else:
        console.print(f"[red][FAIL][/red] {result.message}")
        raise typer.Exit(code=1)




@app.command()
def config():
    """Show current configuration (secrets masked)."""
    show_banner()
    
    settings = get_settings()
    
    table = Table(title="Configuration", show_header=True)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")
    
    # Source/Target settings from semabridge.yaml
    try:
        from semabridge.core.config_loader import get_default_config_path, load_and_merge_configs
        config_path = get_default_config_path()
        if config_path:
            run_config, _ = load_and_merge_configs([config_path], resolve_env=False)
            
            # Source settings
            table.add_section()
            if run_config and run_config.get("source"):
                source = run_config["source"]
                table.add_row("Source Type", source.get("type", "Not configured"))
                if source.get("dataset_id"):
                    table.add_row("Source Dataset ID", source.get("dataset_id"))
            else:
                table.add_row("Source", "Not configured")
            
            # Target settings
            table.add_section()
            if run_config and run_config.get("target"):
                target = run_config["target"]
                table.add_row("Target Type", target.get("type", "Not configured"))
                table.add_row("Target Deploy", str(target.get("deploy", False)))
            else:
                table.add_row("Target", "Not configured")
            
            # Model name from YAML
            if run_config and run_config.get("model_name"):
                table.add_section()
                table.add_row("Model Name (YAML)", run_config["model_name"])
    except Exception:
        pass  # Fall back to showing just env-based settings
    
    # Snowflake settings
    table.add_section()
    table.add_row("Snowflake Account", settings.snowflake.account)
    table.add_row("Snowflake User", settings.snowflake.user)
    table.add_row("Snowflake Password", "********")
    table.add_row("Snowflake Warehouse", settings.snowflake.warehouse)
    table.add_row("Snowflake Database", settings.snowflake.database)
    table.add_row("Snowflake Schema", settings.snowflake.schema_name)
    
    # Fabric settings  
    table.add_section()
    table.add_row("Fabric Tenant ID", settings.fabric.tenant_id[:8] + "..." if len(settings.fabric.tenant_id) > 8 else settings.fabric.tenant_id)
    table.add_row("Fabric Client ID", settings.fabric.client_id[:8] + "..." if len(settings.fabric.client_id) > 8 else settings.fabric.client_id)
    table.add_row("Fabric Client Secret", "********")
    table.add_row("Fabric Workspace ID", settings.fabric.workspace_id)
    
    # Model settings from env
    table.add_section()
    table.add_row("Model Name", settings.model.name)
    table.add_row("Cache Enabled", str(settings.model.cache_enabled))
    
    console.print(table)


@app.command()
def validate(
    model_name: Optional[str] = typer.Option(None, "--model", "-m", help="Specific model to validate (wildcards supported)"),
):
    """
    Validate global config.yaml, project semabridge.yaml, connections, and physical schema.
    
    1. Validates global config.yaml (structure, intermediate_format, LLM settings).
    2. Validates project semabridge.yaml (structure, source/target, env vars).
    3. Tests connectivity to Snowflake and Fabric.
    4. Discovers models and validates physical dependencies (tables, columns) in Snowflake.
    """
    show_banner()
    
    from semabridge.utils.enterprise_logger import get_enterprise_logger
    from semabridge.repository.command_logger import get_command_logger, CommandType, ActionType
    ent_logger = get_enterprise_logger()
    cmd_logger = get_command_logger()
    
    ent_logger.start_session("validate")
    log_entry = cmd_logger.log_start(
        command=CommandType.VALIDATE,
        action_type=ActionType.VALIDATION,
        details={"model": model_name}
    )
    
    start_time = time.time()
    has_failures = False
    try:
        settings = get_settings()
        
        console.print(Panel.fit(
            f"[bold]System Validation[/bold]",
            title="Validate",
        ))

        # ---- Step 1: Validate global config.yaml -------------------------
        console.print("\n[bold]1. Global Configuration (config.yaml)[/bold]")
        try:
            from semabridge.core.global_settings import GlobalConfigManager
            gcm = GlobalConfigManager()
            if gcm.path.exists():
                gc_errors = gcm.validate()
                if gc_errors:
                    has_failures = True
                    console.print(f"  [red]FAIL[/red]")
                    for err in gc_errors:
                        console.print(f"    - {err}")
                else:
                    cfg = gcm.config
                    fmt = cfg.core.intermediate_format.value if cfg else "N/A"
                    llm = "enabled" if (cfg and cfg.llm.enabled) else "disabled"
                    console.print(f"  [green]OK[/green]  (format={fmt}, LLM={llm})")
            else:
                console.print("  [yellow]SKIP[/yellow] config.yaml not found (run 'semabridge init')")
        except Exception as exc:
            has_failures = True
            console.print(f"  [red]FAIL[/red] ({exc})")

        # ---- Step 2: Validate project semabridge.yaml --------------------
        console.print("\n[bold]2. Project Configuration (semabridge.yaml)[/bold]")
        from semabridge.core.config_loader import get_default_config_path, load_and_merge_configs, validate_config_schema
        project_config_path = get_default_config_path()
        if project_config_path:
            try:
                raw_config, _ = load_and_merge_configs([project_config_path], resolve_env=False)
                schema_errors = validate_config_schema(raw_config)
                if schema_errors:
                    has_failures = True
                    console.print(f"  [red]FAIL[/red]")
                    for err in schema_errors:
                        console.print(f"    - {err}")
                else:
                    console.print(f"  [green]OK[/green]  ({project_config_path})")
            except Exception as exc:
                has_failures = True
                console.print(f"  [red]FAIL[/red] ({exc})")
        else:
            console.print("  [yellow]SKIP[/yellow] semabridge.yaml not found")
    
        # ---- Step 3: Connection Tests ------------------------------------
        console.print("\n[bold]3. Connectivity Check[/bold]")
        
        # Snowflake
        console.print("  • Snowflake...", end=" ")
        try:
            from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
            extractor = SnowflakeExtractor(settings.snowflake)
            extractor.test_connection()
            console.print("[green]OK[/green]")
        except Exception as e:
            console.print(f"[red]FAIL[/red] ({e})")
        
        # Fabric
        console.print("  • Fabric...", end=" ")
        try:
            from semabridge.connectors.fabric_publisher import FabricPublisher
            publisher = FabricPublisher(settings.fabric)
            if publisher.test_connection():
                console.print("[green]OK[/green]")
            else:
                console.print("[red]FAIL[/red] (API Error)")
        except Exception as e:
            console.print(f"[red]FAIL[/red] ({e})")
            
        # ---- Step 4: Physical Schema Validation --------------------------
        console.print("\n[bold]4. Physical Schema Validation[/bold]")
        
        from semabridge.core.engine import SemaBridgeEngine
        from semabridge.core.project import ProjectConfig
        
        try:
            if not project_config_path:
                console.print("  [yellow]SKIP[/yellow] semabridge.yaml not found")
            else:
                raw_config, _ = load_and_merge_configs([project_config_path])
                project_config = ProjectConfig.from_yaml_dict(raw_config)
                
                # Override model filter if CLI arg provided
                if model_name:
                    if project_config.source:
                        project_config.source.model = model_name

                engine = SemaBridgeEngine(project_config)
                result = engine.validate_run()
                
                console.print()
                if result.success:
                    console.print(f"  [green]OK[/green] Schema Validation Passed")
                    console.print(f"    Processed: {result.models_processed} models")
                else:
                    has_failures = True
                    console.print(f"  [red]FAIL[/red] Schema Validation Failed")
                    for error in result.errors:
                        console.print(f"    - {error}")
                
        except Exception as e:
            has_failures = True
            console.print(f"  [red]Validation Error:[/red] {e}")
            if settings.logging.level == "DEBUG":
                import traceback
                traceback.print_exc()
        
        if has_failures:
            console.print("\n[red]Validation completed with errors.[/red]")
            raise typer.Exit(code=1)
        else:
            console.print("\n[green]All validations passed.[/green]")
            
        cmd_logger.log_success(log_entry, int((time.time() - start_time) * 1000))
        
    except typer.Exit:
        raise
    except Exception as e:
        duration = int((time.time() - start_time) * 1000)
        cmd_logger.log_failure(log_entry, str(e), duration)
        raise
    finally:
        ent_logger.stop_session()



@app.command()
def list_fabric_models():
    """List all semantic models in the Fabric workspace."""
    show_banner()
    
    settings = get_settings()
    
    console.print(Panel.fit(
        f"[bold]Fabric Semantic Models[/bold]\n"
        f"Workspace: {settings.fabric.workspace_id}",
        title="List Models",
    ))
    
    try:
        extractor = FabricExtractor(settings.fabric)
        models = extractor.list_semantic_models()
        
        if not models:
            console.print("[yellow]No semantic models found in workspace.[/yellow]")
            return
        
        table = Table(title=f"Found {len(models)} Models", show_header=True)
        table.add_column("#", style="dim", width=3)
        table.add_column("ID (use this for --dataset-id)", style="cyan", no_wrap=True)
        table.add_column("Display Name", style="green")
        table.add_column("Description", style="white", max_width=40)
        
        for i, model in enumerate(models, 1):
            table.add_row(
                str(i),
                model.get("id", "N/A"),
                model.get("displayName", "N/A"),
                (model.get("description", "") or "")[:40]
            )
        
        console.print(table)
        console.print("\n[dim]Use the ID column value with: python main.py reverse-sync --dataset-id <ID>[/dim]")
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@app.command()
def extract(
    output: Path = typer.Option(
        Path("output/metadata.json"),
        "--output", "-o",
        help="Output file for extracted metadata",
    ),
    use_cache: bool = typer.Option(
        True,
        "--cache/--no-cache",
        help="Use incremental cache",
    ),
):
    """Extract metadata from Snowflake."""
    show_banner()
    
    from semabridge.utils.enterprise_logger import get_enterprise_logger
    ent_logger = get_enterprise_logger()
    ent_logger.start_session("extract")
    
    try:
        settings = get_settings()
    
        console.print(Panel.fit(
            f"[bold]Extracting metadata from Snowflake[/bold]\n"
            f"Database: {settings.snowflake.database}\n"
            f"Schema: {settings.snowflake.schema_name}",
            title="Extract",
        ))
        
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.utils.cache import MetadataCache
        
        cache = MetadataCache(settings.model.cache_dir) if use_cache else None
        
        extractor = SnowflakeExtractor(
            config=settings.snowflake,
            cache=cache,
            exclude_tables=settings.model.excluded_table_list,
            include_tables=settings.model.included_table_list,
        )
        
        metadata = extractor.extract_all()
        
        # Also read any existing semantic tables
        semantic_data = extractor.read_semantic_tables()
        metadata["semantic_tables"] = semantic_data
        
        # Save output
        output.parent.mkdir(parents=True, exist_ok=True)
        from semabridge.formats.sanitizer import OutputSanitizer
        sanitized_json = OutputSanitizer.normalize_json(metadata)
        with open(output, "w", encoding="utf-8") as f:
            f.write(sanitized_json)
        
        console.print(f"\n[green][OK] Extracted metadata saved to {output}[/green]")
        console.print(f"  Tables: {len(metadata['tables'])}")
        console.print(f"  Foreign Keys: {len(metadata['foreign_keys'])}")
    finally:
        ent_logger.stop_session()


@app.command()
def build(
    input_path: Path = typer.Option(
        Path("output/metadata.json"),
        "--input", "-i",
        help="Input metadata file",
    ),
    output: Path = typer.Option(
        Path("output/sml"),
        "--output", "-o",
        help="Output directory for SML files",
    ),
    auto_detect: bool = typer.Option(
        True,
        "--auto-detect/--no-auto-detect",
        help="Auto-detect relationships, hierarchies, and measures",
    ),
):
    show_banner()
    
    from semabridge.utils.enterprise_logger import get_enterprise_logger
    ent_logger = get_enterprise_logger()
    ent_logger.start_session("build")
    
    try:
        settings = get_settings()
    
        console.print(Panel.fit(
            f"[bold]Building SML model[/bold]\n"
            f"Input: {input_path}\n"
            f"Output: {output}",
            title="Build",
        ))
        
        # Load metadata
        with open(input_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        
        from semabridge.formats.sml.assembler import SMLAssembler
        from semabridge.formats.sml.serializer import SMLSerializer
        from semabridge.connectors.relationship_detector import RelationshipDetector
        from semabridge.connectors.hierarchy_detector import HierarchyDetector
        from semabridge.connectors.measure_detector import MeasureDetector
        
        # Create assembler
        assembler = SMLAssembler(
            model_name=settings.model.name,
            description=settings.model.description,
            source_database=metadata.get("database", ""),
            source_schema=metadata.get("schema", ""),
        )
        
        # Add tables
        for table_name, table_info in metadata.get("tables", {}).items():
            columns = metadata.get("columns", {}).get(table_name, [])
            assembler.add_table(
                table_name=table_name,
                columns=columns,
                description=table_info.get("description", ""),
                row_count=table_info.get("row_count"),
            )
        
        if auto_detect:
            # Detect relationships
            rel_detector = RelationshipDetector(
                tables=metadata.get("tables", {}),
                columns=metadata.get("columns", {}),
                primary_keys=metadata.get("primary_keys", {}),
                explicit_fks=metadata.get("foreign_keys", []),
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
            
            # Detect hierarchies
            hier_detector = HierarchyDetector(
                tables=metadata.get("tables", {}),
                columns=metadata.get("columns", {}),
            )
            
            hierarchies = hier_detector.detect_all()
            for table_name, table_hierarchies in hierarchies.items():
                attributes = [
                    {"name": col["name"], "column": col["name"]}
                    for col in metadata.get("columns", {}).get(table_name, [])
                ]
                for h in table_hierarchies:
                    assembler.add_dimension(
                        name=h["name"],
                        dataset=table_name,
                        attributes=attributes,
                        hierarchies=[h],
                    )
            
            # Detect measures
            measure_detector = MeasureDetector(
                tables=metadata.get("tables", {}),
                columns=metadata.get("columns", {}),
                relationships=relationships,
            )
            
            fact_tables = set(measure_detector.detect_fact_tables())
            for dataset in assembler._datasets:
                if dataset.unique_name in fact_tables:
                    dataset.is_fact = True
            
            all_measures = measure_detector.detect_all_measures()
            for table_name, measures in all_measures.items():
                for measure in measures[:5]:  # Limit to 5 measures per table
                    assembler.add_metric(
                        name=measure["name"],
                        dataset=table_name,
                        source_column=measure["column"],
                        aggregation=measure["aggregation"],
                    )
        
        # Build and save
        sml_model = assembler.build()
        SMLSerializer.save(sml_model, output / "model.yaml")
        
        console.print(f"\n[green][OK] SML model saved to {output}[/green]")
        console.print(f"  Datasets: {sml_model.dataset_count}")
        console.print(f"  Dimensions: {sml_model.dimension_count}")
        console.print(f"  Metrics: {sml_model.metric_count}")
        console.print(f"  Relationships: {sml_model.relationship_count}")
    finally:
        ent_logger.stop_session()


@app.command()
def emit(
    input_path: Path = typer.Option(
        Path("output/sml/model.yaml"),
        "--input", "-i",
        help="Input SML file or directory",
    ),
    output: Path = typer.Option(
        Path("output/model.bim"),
        "--output", "-o",
        help="Output model.bim file",
    ),
):
    """Generate Fabric model.bim from SML."""
    show_banner()
    
    from semabridge.utils.enterprise_logger import get_enterprise_logger
    ent_logger = get_enterprise_logger()
    ent_logger.start_session("emit")
    
    try:
        settings = get_settings()
        
        console.print(Panel.fit(
            f"[bold]Generating model.bim[/bold]\n"
            f"Input: {input_path}\n"
            f"Output: {output}",
            title="Emit",
        ))
    
        from semabridge.formats.sml.serializer import SMLSerializer
        from semabridge.connectors.tmsl_generator import TMSLGenerator
        
        # Load SML
        sml_model = SMLSerializer.load(input_path)
        
        # Generate TMSL
        generator = TMSLGenerator(
            sml_model,
            snowflake_server=settings.snowflake.account,
            snowflake_warehouse=settings.snowflake.warehouse,
            snowflake_database=settings.snowflake.database,
            snowflake_schema=settings.snowflake.schema_name,
        )
        
        generator.save(output)
        
        summary = generator.get_summary()
        console.print(f"\n[green][OK] Model.bim generated: {output}[/green]")
        console.print(f"  Tables: {summary['tables']}")
        console.print(f"  Columns: {summary['total_columns']}")
        console.print(f"  Measures: {summary['measures']}")
        console.print(f"  Relationships: {summary['relationships']}")
    finally:
        ent_logger.stop_session()


@app.command()
def publish(
    input_path: Path = typer.Option(
        Path("output/sml/model.yaml"),
        "--input", "-i",
        help="Input SML file or directory",
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name", "-n",
        help="Override model name",
    ),
    overwrite: bool = typer.Option(
        True,
        "--overwrite/--no-overwrite",
        help="Overwrite existing model",
    ),
):
    """Publish semantic model to Fabric."""
    show_banner()
    
    from semabridge.utils.enterprise_logger import get_enterprise_logger
    ent_logger = get_enterprise_logger()
    ent_logger.start_session("publish")
    
    try:
        settings = get_settings()
        model_name = name or settings.model.name
        
        console.print(Panel.fit(
            f"[bold]Publishing to Fabric[/bold]\n"
            f"Model: {model_name}\n"
            f"Workspace: {settings.fabric.workspace_id}",
            title="Publish",
        ))
    
        from semabridge.formats.sml.serializer import SMLSerializer
        from semabridge.connectors.fabric_publisher import FabricPublisher
        
        # Load SML
        sml_model = SMLSerializer.load(input_path)
        
        # Publish
        publisher = FabricPublisher(settings.fabric)
        
        result = publisher.publish(
            sml_model=sml_model,
            model_name=model_name,
            snowflake_server=settings.snowflake.account,
            snowflake_warehouse=settings.snowflake.warehouse,
            snowflake_database=settings.snowflake.database,
            snowflake_schema=settings.snowflake.schema_name,
            overwrite=overwrite,
        )
        
        console.print(f"\n[green][OK] Published to Fabric![/green]")
        console.print(f"  Model ID: {result.get('id', 'N/A')}")
        console.print(f"  Display Name: {result.get('displayName', model_name)}")
    finally:
        ent_logger.stop_session()


@app.command(name="sync-measures")
def sync_measures(
    dataset_id: str = typer.Option(..., "--dataset-id", "-d", help="Fabric Dataset ID to sync measures from"),
    workspace_id: Optional[str] = typer.Option(None, "--workspace-id", "-w", help="Optional Fabric Workspace ID override"),
    measures: Optional[str] = typer.Option(None, "--measures", "-m", help="Comma-separated measure names to sync (default: all syncable)"),
    dimensions: Optional[str] = typer.Option(None, "--dimensions", help="Override dimensions for evaluation context"),
    partition_by: Optional[str] = typer.Option("'Date'[Year]", "--partition-by", help="Partition dimension for large datasets"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be synced without executing"),
):
    """
    Sync complex DAX measures from Fabric to Snowflake.
    
    Evaluates DAX measures via Fabric API and writes results to 
    MEASURES_<name> tables in Snowflake for use in Cortex Analyst.
    
    This is useful for measures that cannot be translated to SQL
    (Time Intelligence, CALCULATE with filters, etc.)
    
    Example:
        semabridge sync-measures --dataset-id abc-123
        semabridge sync-measures --dataset-id abc-123 --measures "Sales YTD,Revenue MTD"
    """
    show_banner()
    
    settings = get_settings()
    
    if workspace_id:
        settings.fabric.workspace_id = workspace_id
    
    console.print(Panel.fit(
        f"[bold]Sync DAX Measures[/bold]\n"
        f"Dataset: {dataset_id}\n"
        f"Target: Snowflake {settings.snowflake.database}.{settings.snowflake.schema_name}",
        title="Measure Sync",
    ))
    
    try:
        # Extract model definition
        console.print("\n[bold cyan]Step 1/3: Extracting model definition...[/bold cyan]")
        extractor = FabricExtractor(settings.fabric)
        
        # Get model info
        fabric_source = extractor.get_model_definition(dataset_id=dataset_id)
        if not fabric_source:
            console.print("[red]Error: Could not extract model definition[/red]")
            raise typer.Exit(code=1)
        
        # Convert to OSI to get measure metadata
        console.print("\n[bold cyan]Step 2/3: Analyzing measures (via OSI)...[/bold cyan]")
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
        
        tmsl_converter = TMSLToOSIConverter()
        source_data = {
            "tmsl": fabric_source,
            "workspace_id": settings.fabric.workspace_id,
            "dataset_id": dataset_id
        }
        osi_model = tmsl_converter.to_osi(source_data)
        
        # Run Tiered Safety Pipeline directly on OSI model
        from semabridge.converter.safety_pipeline import TieredSafetyPipeline
        override_dir = Path("output/manual_sql_overrides")
        override_dir.mkdir(parents=True, exist_ok=True)

        safety_pipeline = TieredSafetyPipeline(
            override_dir=override_dir,
            database=settings.snowflake.database if hasattr(settings, 'snowflake') else "ANALYTICS_DB",
            schema=settings.snowflake.schema_name if hasattr(settings, 'snowflake') else "SEMANTIC",
        )
        safety_result = safety_pipeline.run_osi(osi_model)
        safety_report = safety_result.get_report()

        # Filter to syncable measures (updated by safety pipeline on OSI metrics)
        syncable = [m for m in osi_model.metrics if m.sync_enabled and not m.is_hidden]
        
        # Apply measure filter if specified
        if measures:
            measure_names = [m.strip() for m in measures.split(",")]
            syncable = [m for m in syncable if m.unique_name in measure_names]
        
        console.print(f"  Found {len(osi_model.metrics)} total measures")
        console.print(f"  Syncable: {len(syncable)}")
        
        # Classification by tier (from safety pipeline)
        tier_table = Table(title="Tiered Safety Classification")
        tier_table.add_column("Tier", style="cyan")
        tier_table.add_column("Description", style="white")
        tier_table.add_column("Count", justify="right")
        tier_table.add_column("Status", style="green")
        
        tier_table.add_row("1", "Simple Aggregations", str(safety_report["tier_distribution"][1]), "[green]Auto-translated[/green]")
        tier_table.add_row("2", "Arithmetic/Branching", str(safety_report["tier_distribution"][2]), "[green]Auto-translated[/green]")
        tier_table.add_row("3", "Context-Dependent", str(safety_report["tier_distribution"][3]), "[yellow]Override Required[/yellow]")
        tier_table.add_row("4", "Structural Hazard", str(safety_report["tier_distribution"][4]), "[red]Override Required[/red]")
        
        console.print(tier_table)
        
        if safety_report["override_required"] > 0:
            console.print(
                f"  Override coverage: {safety_report['override_coverage_pct']}% "
                f"({len(safety_result.overrides_loaded)} of {safety_report['override_required']} measures)"
            )
        
        if dry_run:
            console.print("\n[yellow]Dry run - no data synced[/yellow]")
            
            # List measures that would be synced
            if syncable:
                console.print("\n[bold]Measures to sync:[/bold]")
                for m in syncable:
                    dims = m.group_by_dimensions or ["'Date'[Year]"]
                    console.print(f"  • {m.unique_name} (Tier {m.complexity_tier}, dims: {len(dims)})")
            return
        
        # Step 3: Execute sync
        console.print("\n[bold cyan]Step 3/3: Syncing measures to Snowflake...[/bold cyan]")
        
        emitter = SnowflakeEmitter(settings.snowflake)
        
        results = emitter.sync_all_measures_from_osi(
            osi=osi_model,
            fabric_extractor=extractor,
            dataset_id=dataset_id,
        )
        
        # Display results
        result_table = Table(title="Sync Results")
        result_table.add_column("Measure", style="cyan")
        result_table.add_column("Status", style="white")
        result_table.add_column("Rows", justify="right")
        result_table.add_column("Details", style="dim")
        
        for name, result in results.items():
            status = result.get("status", "unknown")
            rows = result.get("rows", 0)
            error = result.get("error", "")
            
            status_display = {
                "success": "[green]✓ Success[/green]",
                "failed": "[red]✗ Failed[/red]",
                "empty": "[yellow]○ Empty[/yellow]",
                "skipped": "[dim]- Skipped[/dim]",
            }.get(status, status)
            
            result_table.add_row(
                name,
                status_display,
                str(rows) if status == "success" else "-",
                error[:40] if error else "",
            )
        
        console.print(result_table)
        
        # Summary
        success = sum(1 for r in results.values() if r.get("status") == "success")
        failed = sum(1 for r in results.values() if r.get("status") == "failed")
        total_rows = sum(r.get("rows", 0) for r in results.values())
        
        console.print(f"\n[bold]Summary:[/bold] {success} success, {failed} failed, {total_rows:,} total rows")
        
        if success > 0:
            console.print(f"\n[green]✓ Measures synced to MEASURES_* tables in Snowflake[/green]")
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(code=1)






@app.command()
def history(
    dataset_id: str = typer.Option(..., "--dataset-id", "-d", help="Fabric Dataset ID (project ID)"),
    limit: int = typer.Option(10, "--limit", "-l", help="Number of snapshots to show"),
):
    """
    View version history for a semantic model.
    
    Shows all snapshots stored in DuckDB for the given dataset.
    """
    show_banner()
    
    console.print(Panel.fit(
        f"[bold]Version History[/bold]\n"
        f"Dataset: {dataset_id}",
        title="History",
    ))
    
    db_manager = DuckDBManager()
    snapshots = db_manager.list_snapshots(dataset_id, limit=limit)
    
    if not snapshots:
        console.print("[yellow]No snapshots found for this dataset.[/yellow]")
        console.print(f"Run: python main.py reverse-sync -d {dataset_id} --sync --tag v1.0")
        return
    
    head = db_manager.get_head(dataset_id)
    
    table = Table(title=f"Snapshot History ({len(snapshots)} versions)")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Snapshot ID", style="cyan")
    table.add_column("Tag", style="green")
    table.add_column("Status", style="bold")
    table.add_column("Dur (ms)", justify="right")
    table.add_column("Timestamp", style="white")
    table.add_column("Metrics", style="magenta", justify="right")
    table.add_column("HEAD", style="yellow")
    
    for i, snap in enumerate(snapshots):
        metrics = len(snap.sml_blob.get("metrics", []))
        is_head = "●" if snap.snapshot_id == head.snapshot_id else ""
        
        status_color = "[green]" if snap.status == "success" else "[red]"
        status_display = f"{status_color}{snap.status}[/]"
        
        table.add_row(
            str(i + 1),
            snap.snapshot_id[:12] + "...",
            snap.version_tag or "-",
            status_display,
            str(snap.duration_ms or "-"),
            snap.timestamp[:19],
            str(metrics),
             is_head
        )
    
    console.print(table)
    console.print(f"\n[dim]Use 'rollback' command to restore a previous version[/dim]")


@app.command()
def rollback(
    dataset_id: str = typer.Option(..., "--dataset-id", "-d", help="Fabric Dataset ID (project ID)"),
    snapshot_id: Optional[str] = typer.Option(None, "--snapshot-id", "-s", help="Target snapshot ID to rollback to"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Target version tag to rollback to (e.g., v1.0)"),
    sync: bool = typer.Option(False, "--sync", help="Sync rolled-back version bidirectionally to Snowflake and Fabric"),
    rollback_tag: Optional[str] = typer.Option(None, "--rollback-tag", help="Tag for the rollback snapshot"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show preview only, do not execute"),
):
    """
    Rollback to a previous version of the semantic model.
    
    TWO-PHASE ROLLBACK FLOW:
    
    Phase 1 (Preview): Shows semantic diff of what will change
    Phase 2 (Confirmation): Asks for user confirmation before proceeding
    
    Use --sync to also update Snowflake AND Fabric with the rolled-back version.
    Use --yes to skip the confirmation prompt.
    Use --dry-run to only show what would change without executing.
    
    Rollback is non-destructive - the old history is preserved.
    """
    from semabridge.repository.command_logger import (
        get_command_logger, CommandType, ActionType
    )
    from semabridge.repository.semantic_diff_engine import SemanticDiffEngine
    
    show_banner()
    settings = get_settings()
    start_time = time.time()
    run_id = str(uuid.uuid4())
    
    # Initialize command logging
    cmd_logger = get_command_logger()
    log_entry = cmd_logger.log_start(
        command=CommandType.ROLLBACK,
        action_type=ActionType.ROLLBACK_OPERATION,
        project_id=dataset_id,
        details={
            "target_tag": tag,
            "target_snapshot": snapshot_id,
            "sync": sync,
            "run_id": run_id,
        }
    )
    
    if not snapshot_id and not tag:
        console.print("[red]Error: Must specify either --snapshot-id or --tag[/red]")
        cmd_logger.log_failure(log_entry, "Missing target version", int((time.time() - start_time) * 1000))
        raise typer.Exit(code=1)
    
    console.print(Panel.fit(
        f"[bold]Rollback[/bold]\n"
        f"Dataset: {dataset_id}\n"
        f"Target: {tag or snapshot_id[:12] + '...'}\n"
        f"Sync Bidirectionally: {'Yes' if sync else 'No'}",
        title="Rollback",
    ))
    
    try:
        db_manager = DuckDBManager()
        diff_engine = SemanticDiffEngine()
        
        # ================================================================
        # STEP 1: Resolve target and current versions
        # ================================================================
        console.print("\n[bold cyan]Step 1/4: Resolving Versions[/bold cyan]")
        
        # Get current HEAD
        current = db_manager.get_head(dataset_id)
        if not current:
            console.print(f"[red]Error: No snapshots found for '{dataset_id}'[/red]")
            cmd_logger.log_failure(log_entry, "No snapshots found", int((time.time() - start_time) * 1000))
            raise typer.Exit(code=1)
        
        # Find target snapshot
        if tag:
            target = db_manager.get_snapshot_by_tag(dataset_id, tag)
            if not target:
                console.print(f"[red]Error: No snapshot found with tag '{tag}'[/red]")
                cmd_logger.log_failure(log_entry, f"Tag not found: {tag}", int((time.time() - start_time) * 1000))
                raise typer.Exit(code=1)
        else:
            target = db_manager.get_snapshot(snapshot_id)
            if not target:
                console.print(f"[red]Error: Snapshot '{snapshot_id}' not found[/red]")
                cmd_logger.log_failure(log_entry, f"Snapshot not found: {snapshot_id}", int((time.time() - start_time) * 1000))
                raise typer.Exit(code=1)
        
        console.print(f"  Current (HEAD): {current.version_tag or current.snapshot_id[:12]} ({current.timestamp[:19]})")
        console.print(f"  Target: {target.version_tag or target.snapshot_id[:12]} ({target.timestamp[:19]})")
        
        # Check if already at target
        if current.snapshot_id == target.snapshot_id:
            console.print("\n[yellow]Already at target version. Nothing to rollback.[/yellow]")
            cmd_logger.log_success(log_entry, int((time.time() - start_time) * 1000), {"result": "no_change"})
            return
        
        # ================================================================
        # STEP 2: PREVIEW PHASE - Generate Semantic Diff
        # ================================================================
        console.print("\n[bold cyan]Step 2/4: Preview - Changes After Rollback[/bold cyan]")
        cmd_logger.log_phase(log_entry, "preview", {"phase": "preview"})
        
        # Compare current → target (what changes when we rollback)
        diff = diff_engine.compare_states(
            state_1=current.sml_blob,
            state_2=target.sml_blob,
            from_snapshot_id=f"CURRENT ({current.version_tag or current.snapshot_id[:8]})",
            to_snapshot_id=f"TARGET ({target.version_tag or target.snapshot_id[:8]})",
            from_adapter="repository",
            to_adapter="repository",
        )
        
        # Get real change count using normalized comparison (filters false positives)
        real_changes = db_manager.compare_versions(
            current.project_id,
            current.snapshot_id,
            target.snapshot_id
        )
        real_change_count = len(real_changes)
        
        # Display semantic diff (uses real_change_count internally)
        _display_rollback_preview(diff, current, target)
        
        # Log preview details
        log_entry.details.update({
            "preview_total_changes": real_change_count,
            "preview_breaking_changes": len(diff.breaking_changes),
            "preview_measures_added": diff.summary.measures_added,
            "preview_measures_removed": diff.summary.measures_removed,
            "preview_datasets_added": diff.summary.datasets_added,
            "preview_datasets_removed": diff.summary.datasets_removed,
        })
        
        # If dry run, stop here
        if dry_run:
            console.print("\n[yellow]Dry run mode - rollback not executed.[/yellow]")
            cmd_logger.log_phase(log_entry, "preview", {"result": "dry_run"})
            cmd_logger.log_success(log_entry, int((time.time() - start_time) * 1000), {"result": "dry_run"})
            return
        
        # ================================================================
        # STEP 3: CONFIRMATION PHASE
        # ================================================================
        console.print("\n[bold cyan]Step 3/4: Confirmation[/bold cyan]")
        
        # If no real changes, skip confirmation
        if real_change_count == 0:
            console.print("  [green]✓ No changes to apply. Versions are identical.[/green]")
            cmd_logger.log_success(log_entry, int((time.time() - start_time) * 1000), {"result": "no_changes"})
            return
        
        if not yes:
            # Show confirmation prompt
            console.print()
            console.print(Panel.fit(
                f"[bold yellow]⚠ Rollback will apply {real_change_count} changes[/bold yellow]\n\n"
                f"From: {current.version_tag or current.snapshot_id[:12]}\n"
                f"To: {target.version_tag or target.snapshot_id[:12]}\n\n"
                + (f"[red]Including {len(diff.breaking_changes)} breaking changes![/red]\n" if diff.breaking_changes else "")
                + f"Sync to Snowflake & Fabric: {'Yes' if sync else 'No'}",
                title="⚠ Confirm Rollback",
                border_style="yellow",
            ))
            
            confirm = typer.confirm("Do you want to proceed with rollback?", default=False)
            
            if not confirm:
                console.print("\n[yellow]Rollback aborted by user.[/yellow]")
                cmd_logger.log_aborted(log_entry, "User cancelled", int((time.time() - start_time) * 1000))
                raise typer.Exit(code=0)
        
        # User confirmed
        cmd_logger.log_phase(log_entry, "confirmed", {"phase": "confirmed"})
        console.print("  [green]✓[/green] Confirmed")
        
        # ================================================================
        # STEP 4: EXECUTE ROLLBACK
        # ================================================================
        console.print("\n[bold cyan]Step 4/4: Executing Rollback[/bold cyan]")
        
        rb_tag = rollback_tag or f"rollback_to_{target.version_tag or target.snapshot_id[:8]}"
        success, new_snapshot_id, changes = db_manager.rollback(dataset_id, target.snapshot_id, rb_tag)
        
        if success:
            console.print(f"  [green][OK][/green] Created rollback snapshot: {new_snapshot_id[:12]}...")
            console.print(f"  Tag: {rb_tag}")
            
            # Sync if requested
            if sync:
                from semabridge.formats.sml.models import SMLModel
                sml_model = SMLModel.model_validate(target.sml_blob)
                
                console.print("\n  Deploying to Snowflake...")
                emitter = SnowflakeEmitter(settings.snowflake)
                emitter.deploy(sml_model)
                console.print(f"  [green][OK][/green] Snowflake Semantic Views updated")

                console.print("  Deploying to Fabric...")
                from semabridge.connectors.fabric_publisher import FabricPublisher
                publisher = FabricPublisher(settings.fabric)
                publisher.publish(
                    sml_model=sml_model,
                    model_name=dataset_id,
                    snowflake_server=settings.snowflake.account,
                    snowflake_warehouse=settings.snowflake.warehouse,
                    snowflake_database=settings.snowflake.database,
                    snowflake_schema=settings.snowflake.schema_name,
                    overwrite=True
                )
                console.print(f"  [green][OK][/green] Fabric Semantic Model updated")
            else:
                console.print("  [yellow]Skipping synchronization (use --sync to execute)[/yellow]")
        else:
            console.print(f"  [yellow]No changes needed (already at target state)[/yellow]")
        
        duration_ms = int((time.time() - start_time) * 1000)
        console.print(f"\n[green][OK] Rollback complete![/green]")
        console.print(f"[dim]Duration: {duration_ms}ms[/dim]")
        
        # Log success
        cmd_logger.log_success(log_entry, duration_ms, {
            "result": "success",
            "new_snapshot_id": new_snapshot_id if success else None,
            "synced": sync,
            "changes_applied": diff.summary.total_changes,
        })
        
    except typer.Exit:
        raise  # Re-raise Exit to preserve exit code
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        console.print(f"\n[red]Error: Rollback execution failed[/red]")
        console.print(f"[yellow]Cause:[/yellow] {str(e)}")
        console.print(f"[blue]Fix:[/blue] Verify the target snapshot ID and your database connectivity.")
        
        cmd_logger.log_failure(log_entry, str(e), duration_ms)
        if settings.logging.level == "DEBUG":
            import traceback
            traceback.print_exc()
        raise typer.Exit(code=1)


def _display_rollback_preview(diff, current, target):
    """Display the semantic diff preview for rollback with tabular format."""
    from rich.tree import Tree
    from semabridge.repository.duckdb_manager import DuckDBManager
    
    # Use DuckDBManager.compare_versions for accurate diff (filters false positives)
    db_manager = DuckDBManager()
    real_changes = db_manager.compare_versions(
        current.project_id,
        current.snapshot_id,  # From (current HEAD)
        target.snapshot_id     # To (target rollback version)
    )
    
    # Summary
    console.print()
    real_change_count = len(real_changes)
    summary_text = f"[bold]Changes if rollback proceeds:[/bold]\n\n"
    summary_text += f"Total Changes: {real_change_count}\n"
    
    if diff.summary.has_breaking_changes:
        summary_text += f"[red]⚠ Breaking Changes: {len(diff.breaking_changes)}[/red]\n"
    
    console.print(Panel(summary_text.strip(), title="Impact Summary", border_style="cyan"))
    
    # If no real changes, show message and return early
    if real_change_count == 0:
        console.print("\n  [green]✓ No semantic changes detected. Versions are identical.[/green]")
        return
    
    # ============================================================
    # TABULAR DIFF: Previous Version | New Version
    # ============================================================
    console.print("\n[bold cyan]Changes (Previous → New):[/bold cyan]")
    
    table = Table(
        title="Rollback Diff: Previous Version → New Version",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Object", style="bold", width=30)
    table.add_column("Previous Value", style="red", width=40)
    table.add_column("New Value", style="green", width=40)
    
    for row in real_changes[:20]:  # Limit to 20 rows for readability
        prev = row["previous_version"][:60] + "..." if len(row["previous_version"]) > 60 else row["previous_version"]
        new = row["new_version"][:60] + "..." if len(row["new_version"]) > 60 else row["new_version"]
        table.add_row(row["object"], prev, new)
    
    console.print()
    console.print(table)
    
    if len(real_changes) > 20:
        console.print(f"[dim]... and {len(real_changes) - 20} more changes[/dim]")
    
    console.print()
    
    # ============================================================
    # Tree view summary by change type
    # ============================================================
    added = [r for r in real_changes if r["previous_version"] == "—"]
    removed = [r for r in real_changes if r["new_version"] == "—"]
    modified = [r for r in real_changes if r["previous_version"] != "—" and r["new_version"] != "—"]
    
    if added or removed or modified:
        tree = Tree(f"[bold]CHANGES[/bold] ({len(real_changes)} total)")
        
        if added:
            branch = tree.add(f"[green]↩ Will be RESTORED ({len(added)})[/green]")
            for c in added[:5]:
                branch.add(f"[green]{c['object']}[/green]")
            if len(added) > 5:
                branch.add(f"[dim]... and {len(added) - 5} more[/dim]")
        
        if modified:
            branch = tree.add(f"[yellow]~ Will be MODIFIED ({len(modified)})[/yellow]")
            for c in modified[:5]:
                branch.add(f"[yellow]{c['object']}[/yellow]")
            if len(modified) > 5:
                branch.add(f"[dim]... and {len(modified) - 5} more[/dim]")
        
        if removed:
            branch = tree.add(f"[red]✗ Will be REMOVED ({len(removed)})[/red]")
            for c in removed[:5]:
                branch.add(f"[red]{c['object']}[/red]")
            if len(removed) > 5:
                branch.add(f"[dim]... and {len(removed) - 5} more[/dim]")
        
        console.print(tree)
        console.print()


@app.command()
def list_projects():
    """
    List all tracked projects (semantic models) in DuckDB.
    """
    show_banner()
    
    console.print(Panel.fit(
        "[bold]Tracked Projects[/bold]\n"
        "All semantic models with version history",
        title="Projects",
    ))
    
    db_manager = DuckDBManager()
    conn = db_manager._get_connection()
    
    try:
        results = conn.execute("""
            SELECT p.project_id, p.name, p.workspace_id, p.last_updated,
                   COUNT(s.snapshot_id) as snapshot_count, p.adapter
            FROM projects p
            LEFT JOIN snapshots s ON p.project_id = s.project_id
            GROUP BY p.project_id, p.name, p.workspace_id, p.last_updated, p.adapter
            ORDER BY p.last_updated DESC
        """).fetchall()
        
        if not results:
            console.print("[yellow]No projects found.[/yellow]")
            console.print("Run 'reverse-sync' to track a Fabric model.")
            return
        
        table = Table(title=f"Tracked Projects ({len(results)})")
        table.add_column("Project ID", style="cyan", no_wrap=True)
        table.add_column("Name", style="green")
        table.add_column("Adapter", style="blue")
        table.add_column("Snapshots", style="magenta", justify="right")
        table.add_column("Last Updated", style="white")
        
        for r in results:
            table.add_row(
                r[0],
                r[1] or "-",
                r[5] or "fabric",
                str(r[4]),
                str(r[3])[:19] if r[3] else "-"
            )
        
        console.print(table)
        
    finally:
        conn.close()


def _run_snowflake_to_fabric(settings, name, dry_run, output_dir, log_entry=None, model_pattern=None):
    import fnmatch

    model_name = name or settings.model.name
    
    console.print(Panel.fit(
        f"[bold]Deploy: Snowflake -> Fabric[/bold]\n"
        f"Model: {model_name}\n"
        f"{'[yellow]DRY RUN - No publish[/yellow]' if dry_run else 'Publishing to Fabric'}",
        title="Deploy",
    ))
    
    start_time = time.time()
    run_id = str(uuid.uuid4())
    
    # Import command logger
    from semabridge.repository.command_logger import get_command_logger, CommandType, ActionType
    cmd_logger = get_command_logger()
    if not log_entry:
        log_entry = cmd_logger.log_start(
            command=CommandType.DEPLOY,
            action_type=ActionType.DEPLOYMENT,
            project_id=model_name,
            details={"source": "snowflake", "target": "fabric", "dry_run": dry_run}
        )
    
    try:
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.connectors.relationship_detector import RelationshipDetector
        from semabridge.formats.sml.assembler import SMLAssembler
        from semabridge.formats.sml.serializer import SMLSerializer
        from semabridge.connectors.tmsl_generator import TMSLGenerator
        from semabridge.connectors.fabric_publisher import FabricPublisher
        from semabridge.utils.cache import MetadataCache
        from semabridge.repository.duckdb_manager import DuckDBManager
        from semabridge.connectors.inference_engine import SmlInferenceEngine

        # Step 1: Extract
        console.print("\n[bold cyan]Step 1/4: Extracting Snowflake metadata...[/bold cyan]")
        cache = MetadataCache(settings.model.cache_dir) if settings.model.cache_enabled else None
        extractor = SnowflakeExtractor(
            config=settings.snowflake,
            cache=cache,
            exclude_tables=settings.model.excluded_table_list,
            include_tables=settings.model.included_table_list,
        )
        metadata = extractor.extract_all()
        semantic_data = extractor.read_semantic_tables()

        semantic_views = extractor.list_semantic_views()
        semantic_view_names = [sv.get("id") for sv in semantic_views if sv.get("id")]
        selected_sv_names = []
        if model_pattern:
            selected_sv_names = [
                sv for sv in semantic_view_names
                if fnmatch.fnmatch(sv.lower(), model_pattern.lower())
            ]
        elif name and name in semantic_view_names:
            selected_sv_names = [name]
        elif len(semantic_view_names) == 1:
            selected_sv_names = [semantic_view_names[0]]

        sv_ddls = []
        for sv_name in selected_sv_names:
            sv_ddls.append(extractor.read_semantic_view_ddl(sv_name))

        if selected_sv_names:
            console.print(f"  [green][OK][/green] Loaded semantic view DDL for: {', '.join(selected_sv_names)}")
        console.print(f"  [green][OK][/green] Extracted {len(metadata['tables'])} tables")

        # Step 2: Build SML
        console.print("\n[bold cyan]Step 2/4: Building SML model...[/bold cyan]")
        assembler = SMLAssembler(
            model_name=model_name,
            description=settings.model.description,
            source_database=metadata.get("database", ""),
            source_schema=metadata.get("schema", ""),
            normalize_names=False,
        )
        
        # Add tables
        for table_name, table_info in metadata.get("tables", {}).items():
            columns = metadata.get("columns", {}).get(table_name, [])
            assembler.add_table(
                table_name=table_name,
                columns=columns,
                description=table_info.get("description", ""),
                row_count=table_info.get("row_count"),
            )
            
        # Detect relationships
        rel_detector = RelationshipDetector(
            tables=metadata.get("tables", {}),
            columns=metadata.get("columns", {}),
            primary_keys=metadata.get("primary_keys", {}),
            explicit_fks=metadata.get("foreign_keys", []),
            include_inferred=False,
        )
        relationships = rel_detector.detect_all()

        semantic_relationships = semantic_data.get("relationships", [])
        ddl_relationships = []
        for sv_ddl in sv_ddls:
            ddl_relationships.extend(sv_ddl.get("relationships", []))

        if semantic_relationships:
            merged = {
                (
                    r["from_table"].upper(),
                    r["from_column"].upper(),
                    r["to_table"].upper(),
                    r["to_column"].upper(),
                ): r
                for r in relationships
            }
            for sr in semantic_relationships:
                key = (
                    sr["from_table"].upper(),
                    sr["from_column"].upper(),
                    sr["to_table"].upper(),
                    sr["to_column"].upper(),
                )
                merged[key] = {
                    "name": sr.get("name", f"REL_{sr['from_table']}_{sr['to_table']}"),
                    "from_table": sr["from_table"],
                    "from_column": sr["from_column"],
                    "to_table": sr["to_table"],
                    "to_column": sr["to_column"],
                    "source": "semantic_override",
                    "confidence": 1.0,
                }

            for sr in ddl_relationships:
                key = (
                    sr["from_table"].upper(),
                    sr["from_column"].upper(),
                    sr["to_table"].upper(),
                    sr["to_column"].upper(),
                )
                merged[key] = {
                    "name": sr.get("name", f"REL_{sr['from_table']}_{sr['to_table']}"),
                    "from_table": sr["from_table"],
                    "from_column": sr["from_column"],
                    "to_table": sr["to_table"],
                    "to_column": sr["to_column"],
                    "source": "semantic_view_ddl",
                    "confidence": 1.0,
                }
            relationships = list(merged.values())
        elif ddl_relationships:
            merged = {
                (
                    r["from_table"].upper(),
                    r["from_column"].upper(),
                    r["to_table"].upper(),
                    r["to_column"].upper(),
                ): r
                for r in relationships
            }
            for sr in ddl_relationships:
                key = (
                    sr["from_table"].upper(),
                    sr["from_column"].upper(),
                    sr["to_table"].upper(),
                    sr["to_column"].upper(),
                )
                merged[key] = {
                    "name": sr.get("name", f"REL_{sr['from_table']}_{sr['to_table']}"),
                    "from_table": sr["from_table"],
                    "from_column": sr["from_column"],
                    "to_table": sr["to_table"],
                    "to_column": sr["to_column"],
                    "source": "semantic_view_ddl",
                    "confidence": 1.0,
                }
            relationships = list(merged.values())

        for rel in relationships:
            assembler.add_relationship(rel["name"], rel["from_table"], rel["from_column"], rel["to_table"], rel["to_column"])

        # Semantic Inference
        engine = SmlInferenceEngine(
            tables=metadata.get("tables", {}),
            columns=metadata.get("columns", {}),
            relationships=relationships,
            primary_keys=metadata.get("primary_keys", {})
        )
        scores = engine.classify()
        classification_map = {}
        for ds in assembler._datasets:
            score = scores.get(ds.unique_name)
            if score:
                classification = score.classification
                classification_map[ds.unique_name] = classification
                if classification == "FACT": ds.is_fact = True
                elif classification == "TIME": ds.is_fact = False
                else: ds.is_fact = False
                
        # Semantic Data (strict mode: semantic-defined measures only)
        ddl_measures = []
        for sv_ddl in sv_ddls:
            ddl_measures.extend(sv_ddl.get("measures", []))

        has_sv_ddl = any(bool(sv_ddl.get("ddl_raw")) for sv_ddl in sv_ddls)

        seen_metric_keys = set()

        for measure in ddl_measures:
            dataset_name = measure.get("table_name", "")
            metric_key = (dataset_name.upper(), measure.get("name", "").upper())
            if metric_key in seen_metric_keys:
                continue
            seen_metric_keys.add(metric_key)
            assembler.add_metric(
                name=measure["name"],
                dataset=dataset_name,
                source_column=measure.get("source_column", ""),
                aggregation=measure.get("aggregation", "sum"),
                expression=measure.get("expression", ""),
                description=measure.get("description", ""),
            )

        # Important: when SV DDL is present, skip _SEMANTIC_MEASURES to avoid
        # re-importing historical auto-generated column measures.
        if not has_sv_ddl:
            for measure in semantic_data.get("measures", []):
                dataset_name = measure.get("table_name", "")
                metric_key = (dataset_name.upper(), measure.get("name", "").upper())
                if metric_key in seen_metric_keys:
                    continue
                seen_metric_keys.add(metric_key)
                assembler.add_metric(
                    name=measure["name"],
                    dataset=dataset_name,
                    source_column=measure.get("source_column", ""),
                    expression=measure["expression"],
                    description=measure.get("description", ""),
                )

        sml_model = assembler.build()
        
        # Note: Build command targets Fabric (TMSL/SML native format).
        # No OSI round-trip needed — SML is the output format for Fabric deployment.
        
        output_dir.mkdir(parents=True, exist_ok=True)
        sml_path = output_dir / "sml" / "model.yaml"
        SMLSerializer.save(sml_model, sml_path)
        console.print(f"  [green][OK][/green] Built OSI-compatible SML model with {sml_model.dataset_count} datasets")

        # Step 3: Versioning
        console.print("\n[bold cyan]Step 3/4: Versioning in DuckDB...[/bold cyan]")
        db_manager = DuckDBManager()
        db_manager.ensure_project(model_name, sml_model.label, settings.fabric.workspace_id, adapter="snowflake")
        sml_dict = sml_model.model_dump(mode='json')
        committed, snapshot_id = db_manager.commit_model(
            project_id=model_name,
            sml_json=sml_dict,
            run_id=run_id,
            initiated_by="cli"
        )
        
        # Step 4: Generate
        console.print("\n[bold cyan]Step 4/5: Generating model.bim...[/bold cyan]")
        generator = TMSLGenerator(
            sml_model,
            snowflake_server=settings.snowflake.account,
            snowflake_warehouse=settings.snowflake.warehouse,
            snowflake_database=settings.snowflake.database,
            snowflake_schema=settings.snowflake.schema_name,
        )
        bim_path = output_dir / "model.bim"
        generator.save(bim_path)
        console.print(f"  [green][OK][/green] Generated {bim_path}")

        # Step 5: Publish
        if dry_run:
            console.print("\n[bold yellow]Step 5/5: SKIPPED (dry run)[/bold yellow]")
        else:
            console.print("\n[bold cyan]Step 5/5: Publishing to Fabric...[/bold cyan]")
            publisher = FabricPublisher(settings.fabric)
            result = publisher.publish(
                sml_model=sml_model,
                model_name=model_name,
                snowflake_server=settings.snowflake.account,
                snowflake_warehouse=settings.snowflake.warehouse,
                snowflake_database=settings.snowflake.database,
                snowflake_schema=settings.snowflake.schema_name,
                overwrite=True,
            )
            console.print(f"  [green][OK][/green] Published to Fabric (ID: {result.get('id')})")
            
        duration = int((time.time() - start_time) * 1000)
        db_manager.commit_model(project_id=model_name, sml_json=sml_dict, status="success", duration_ms=duration, run_id=run_id)
        cmd_logger.log_success(log_entry, duration, {"snapshot_id": snapshot_id})
        console.print(f"\n[green][OK] Deploy complete![/green]")

    except Exception as e:
        duration = int((time.time() - start_time) * 1000)
        console.print(f"\n[red]Error: Snowflake -> Fabric deployment failed[/red]")
        console.print(f"[yellow]Cause:[/yellow] {str(e)}")
        console.print(f"[blue]Fix:[/blue] Check network connectivity and verify Fabric API permissions.")
        
        cmd_logger.log_failure(log_entry, str(e), duration)
        try:
             db_manager = DuckDBManager()
             db_manager.commit_model(project_id=model_name, sml_json={}, status="failed", duration_ms=duration, error_message=str(e), run_id=run_id)
        except: pass
        
        if settings.logging.level == "DEBUG":
            import traceback
            traceback.print_exc()
        raise typer.Exit(code=1)


def _run_fabric_to_snowflake(settings, dataset_id, workspace_id, tag, sync, log_entry=None, model_pattern=None, model_list=None):
    """Run Fabric -> Snowflake sync for one or more models.
    
    When dataset_id is provided, syncs that single model.
    When model_pattern or model_list is provided, discovers and syncs
    each matching model separately.
    """
    ws_id = workspace_id or settings.fabric.workspace_id
    
    from semabridge.connectors.fabric_extractor import FabricExtractor
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.repository.duckdb_manager import DuckDBManager
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.repository.command_logger import get_command_logger, CommandType, ActionType
    
    extractor = FabricExtractor(settings.fabric)
    
    # Resolve which models to sync
    if dataset_id and not model_pattern and not model_list:
        # Single model by dataset_id (legacy path)
        models_to_sync = [{"id": dataset_id, "name": dataset_id}]
    else:
        # Multi-model: discover from workspace
        console.print("[bold cyan]Discovering models...[/bold cyan]")
        all_models = extractor.list_semantic_models()
        
        import fnmatch
        pattern = model_pattern or "*"
        
        if model_list:
            # Explicit list
            models_to_sync = [
                m for m in all_models 
                if m.get("displayName", m.get("id", "")) in model_list
            ]
        else:
            # Glob pattern match
            models_to_sync = [
                m for m in all_models 
                if fnmatch.fnmatch(
                    m.get("displayName", m.get("id", "")).lower(), 
                    pattern.lower()
                )
            ]
        
        # Normalize format
        models_to_sync = [
            {"id": m.get("id", m.get("displayName", "")), "name": m.get("displayName", m.get("id", ""))}
            for m in models_to_sync
        ]
        
        console.print(f"  Found {len(models_to_sync)} model(s) matching pattern '{pattern}'")
        for m in models_to_sync:
            console.print(f"    - {m['name']} ({m['id'][:8]}...)")
    
    if not models_to_sync:
        console.print("[yellow]No models matched the selection criteria.[/yellow]")
        return
    
    # Process each model independently
    total = len(models_to_sync)
    succeeded = 0
    failed = 0
    
    for i, model_info in enumerate(models_to_sync, 1):
        m_id = model_info["id"]
        m_name = model_info["name"]
        
        console.print(f"\n{'='*60}")
        console.print(f"[bold]Model {i}/{total}: {m_name}[/bold]")
        console.print(f"{'='*60}")
        
        try:
            _sync_single_fabric_model(
                settings=settings,
                extractor=extractor,
                dataset_id=m_id,
                model_name=m_name,
                workspace_id=ws_id,
                tag=tag,
                sync=sync,
                log_entry=log_entry,
            )
            succeeded += 1
        except (Exception, SystemExit) as e:
            # typer.Exit is a SystemExit, not Exception — must catch both
            # so a single model failure doesn't kill the entire batch.
            failed += 1
            console.print(f"[red]  Failed: {m_name} — {e}[/red]")
            if total == 1:
                raise  # Re-raise for single-model so caller sees the error
    
    if total > 1:
        console.print(f"\n[bold green]Multi-model sync complete: {succeeded}/{total} succeeded, {failed} failed[/bold green]")
        if failed > 0:
            console.print(f"[yellow]  {failed} model(s) failed. Check output above for details.[/yellow]")


def _sync_single_fabric_model(settings, extractor, dataset_id, model_name, workspace_id, tag, sync, log_entry=None):
    """Sync a single Fabric model to Snowflake."""
    ws_id = workspace_id
    
    console.print(Panel.fit(
        f"[bold]Deploy: Fabric -> Snowflake[/bold]\n"
        f"Dataset: {dataset_id}\n"
        f"Workspace: {ws_id}\n"
        f"Sync Views: {sync}",
        title="Deploy",
    ))
    
    start_time = time.time()
    run_id = str(uuid.uuid4())
    
    from semabridge.repository.command_logger import get_command_logger, CommandType, ActionType
    cmd_logger = get_command_logger()
    if not log_entry:
        log_entry = cmd_logger.log_start(
            command=CommandType.DEPLOY,
            action_type=ActionType.DEPLOYMENT,
            project_id=dataset_id,
            details={"source": "fabric", "target": "snowflake", "sync": sync}
        )
    
    try:
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
        from semabridge.repository.duckdb_manager import DuckDBManager
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        
        # Step 1: Extract
        console.print("\n[bold cyan]Step 1/4: Extracting from Fabric...[/bold cyan]")
        tmsl = extractor.get_model_definition(dataset_id)
        
        console.print("[dim]Fetching table statistics...[/dim]", end="") 
        row_counts = extractor.get_table_row_counts(dataset_id)
        console.print(f" [green][OK] ({len(row_counts)} tables)[/green]")
        
        # Step 2: Transform (TMSL -> OSI, no SML conversion)
        console.print("\n[bold cyan]Step 2/4: Transforming to OSI...[/bold cyan]")
        
        # 2a. TMSL -> OSI
        tmsl_converter = TMSLToOSIConverter()
        source_data = {
            "tmsl": tmsl,
            "workspace_id": ws_id,
            "dataset_id": dataset_id
        }
        osi_model = tmsl_converter.to_osi(source_data)
        console.print(f"  [green][OK][/green] Transformed to OSI ({len(osi_model.metrics)} metrics)")

        # 2b. Tiered Safety Classification (directly on OSI)
        from semabridge.converter.safety_pipeline import TieredSafetyPipeline
        override_dir = Path("output/manual_sql_overrides")
        override_dir.mkdir(parents=True, exist_ok=True)

        safety_pipeline = TieredSafetyPipeline(
            override_dir=override_dir,
            database=settings.snowflake.database if hasattr(settings, 'snowflake') else "ANALYTICS_DB",
            schema=settings.snowflake.schema_name if hasattr(settings, 'snowflake') else "SEMANTIC",
            warehouse=settings.snowflake.warehouse if hasattr(settings, 'snowflake') else "COMPUTE_WH",
        )
        safety_result = safety_pipeline.run_osi(osi_model)
        report = safety_result.get_report()

        # Display safety classification table
        from rich.table import Table as RichTable
        tier_table = RichTable(title="Tiered Safety Classification")
        tier_table.add_column("Tier", style="cyan")
        tier_table.add_column("Description", style="white")
        tier_table.add_column("Count", justify="right")
        tier_table.add_column("Status", style="green")
        tier_table.add_row("1", "Simple Aggregations", str(report["tier_distribution"][1]), "[green]Auto-translated[/green]")
        tier_table.add_row("2", "Arithmetic/Branching", str(report["tier_distribution"][2]), "[green]Auto-translated[/green]")
        tier_table.add_row("3", "Context-Dependent", str(report["tier_distribution"][3]), "[yellow]Override Required[/yellow]")
        tier_table.add_row("4", "Structural Hazard", str(report["tier_distribution"][4]), "[red]Override Required[/red]")
        console.print(tier_table)

        console.print(
            f"  Auto-translatable: {report['auto_translatable']} | "
            f"Override required: {report['override_required']} | "
            f"Override coverage: {report['override_coverage_pct']}%"
        )

        # Write safety report (scoped to model name for multi-model isolation)
        safe_model_name = re.sub(r'[^\w\-.]', '_', model_name)
        report_dir = Path("output/reports") / safe_model_name
        safety_pipeline.write_safety_report(safety_result, report_dir)
        console.print(f"  [green][OK][/green] Safety report saved to {report_dir / 'tiered_safety_report.json'}")
        
        # Step 3: Version Control (OSI JSON)
        console.print("\n[bold cyan]Step 3/4: Versioning in DuckDB...[/bold cyan]")
        db_manager = DuckDBManager()
        db_manager.ensure_project(dataset_id, osi_model.label, ws_id, adapter="fabric")
        
        osi_dict = osi_model.model_dump(mode='json')
        duration = int((time.time() - start_time) * 1000)
        committed, snapshot_id = db_manager.commit_model(
            project_id=dataset_id,
            sml_json=osi_dict,
            tag=tag,
            status="success",
            duration_ms=duration,
            run_id=run_id
        )
        if committed: console.print(f"  [green][OK][/green] Committed: {snapshot_id}")
        else: console.print(f"  [yellow]No changes detected[/yellow]")
        
        # Step 4: Emission/Sync (OSI-native path)
        console.print("\n[bold cyan]Step 4/4: Syncing to Snowflake...[/bold cyan]")
        emitter = SnowflakeEmitter(settings.snowflake)
        output_dir = Path("output/reverse") / safe_model_name
        output_dir.mkdir(parents=True, exist_ok=True)
        
        ddls = emitter.generate_ddls_from_osi(osi_model)
        full_ddl = "\n\n".join(ddls)
        yaml_out = emitter.generate_cortex_yaml_from_osi(osi_model)
        
        with open(output_dir / "semantic_view.sql", "w") as f: f.write(full_ddl)
        with open(output_dir / "cortex_analyst.yaml", "w") as f: f.write(yaml_out)
        console.print(f"  [green][OK][/green] Artifacts generated in {output_dir}")
        
        if sync:
            console.print("  Deploying to Snowflake...")
            emitter.deploy_from_osi(osi_model)
            console.print("  [green][OK][/green] Semantic Views updated")
        else:
             console.print("  [yellow]Skipping sync (use --dry-run=false to execute, default is sync)[/yellow]")
             
        duration = int((time.time() - start_time) * 1000)
        cmd_logger.log_success(log_entry, duration, {"snapshot_id": snapshot_id})
        console.print(f"\n[green][OK] Deploy complete![/green]")
        
    except Exception as e:
        duration = int((time.time() - start_time) * 1000)
        err_msg = str(e)
        console.print(f"\n[red]Error: Fabric -> Snowflake deployment failed[/red]")
        console.print(f"[yellow]Cause:[/yellow] {err_msg}")
        
        # Actionable error guidance
        if "source table" in err_msg.lower() or "does not exist" in err_msg.lower():
            console.print(f"[blue]Fix:[/blue] Ensure source tables exist in Snowflake or enable create_missing_tables in behavior.yaml.")
        elif "invalid identifier" in err_msg.lower():
            console.print(f"[blue]Fix:[/blue] A referenced column does not exist. Check that source tables match the Fabric model schema.")
        elif "timeout" in err_msg.lower() or "semaphore" in err_msg.lower() or "load shedding" in err_msg.lower():
            console.print(f"[blue]Fix:[/blue] Snowflake warehouse is overloaded. Try increasing warehouse size or reducing concurrent syncs.")
        elif "not authorized" in err_msg.lower():
            console.print(f"[blue]Fix:[/blue] Check Snowflake RBAC grants: USAGE on database/schema, CREATE TABLE, CREATE SEMANTIC VIEW.")
        else:
            console.print(f"[blue]Fix:[/blue] Check Snowflake warehouse status and verify Fabric workspace accessibility.")
        
        cmd_logger.log_failure(log_entry, str(e), duration)
        try:
            db_manager = DuckDBManager()
            head = db_manager.get_head(dataset_id)
            sml_json = head.sml_blob if head else {}
            db_manager.commit_model(project_id=dataset_id, sml_json=sml_json, tag=tag, status="failed", duration_ms=duration, error_message=str(e), run_id=run_id)
        except: pass
        
        if settings.logging.level == "DEBUG":
            import traceback
            traceback.print_exc()
        raise typer.Exit(code=1)


@app.command("sync")
def sync(
    source: Optional[str] = typer.Argument(None, help="Source platform (snowflake/fabric). Optional if defined in config."),
    target: Optional[str] = typer.Argument(None, help="Target platform (snowflake/fabric). Inferred if omitted."),
    dataset_id: Optional[str] = typer.Option(None, "--dataset-id", "-d", help="Fabric Dataset ID (single model sync)"),
    workspace_id: Optional[str] = typer.Option(None, "--workspace-id", "-w", help="Fabric Workspace ID"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Version tag"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Override model name"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model name pattern (glob: *, Sale*, *_Prod)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip final publishing"),
    output_dir: Path = typer.Option(Path("output"), "--output-dir", "-o", help="Output directory"),
):
    """
    Synchronize semantic model(s) between platforms.
    
    Each model is synced separately — never merged.
    Use --model '*' to sync all models, or --model 'Sale*' for glob patterns.
    """
    semantic_sync(
        source=source,
        target=target,
        dataset_id=dataset_id,
        workspace_id=workspace_id,
        tag=tag,
        name=name,
        model=model,
        dry_run=dry_run,
        output_dir=output_dir,
    )


@app.command("semantic-sync")
def semantic_sync(
    source: Optional[str] = typer.Argument(None, help="Source platform (snowflake/fabric). Optional if defined in config."),
    target: Optional[str] = typer.Argument(None, help="Target platform (snowflake/fabric). Inferred if omitted."),
    dataset_id: Optional[str] = typer.Option(None, "--dataset-id", "-d", help="Fabric Dataset ID (single model sync)"),
    workspace_id: Optional[str] = typer.Option(None, "--workspace-id", "-w", help="Fabric Workspace ID"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Version tag"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Override model name"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model name pattern (glob: *, Sale*, *_Prod)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip final publishing"),
    output_dir: Path = typer.Option(Path("output"), "--output-dir", "-o", help="Output directory"),
):
    """
    Synchronize semantic model between platforms.
    
    Flows:
    source='snowflake' (target='fabric') -> Extracts from Snowflake, builds SML, deploys to Fabric.
    source='fabric' (target='snowflake') -> Extracts from Fabric, versions in DuckDB, syncs Snowflake views.
    
    Usage:
    semabridge semantic-sync snowflake        # Sync Snowflake -> Fabric
    semabridge semantic-sync fabric           # Sync Fabric -> Snowflake
    semabridge semantic-sync snowflake fabric # Explicit
    """
    show_banner()
    
    from semabridge.utils.enterprise_logger import get_enterprise_logger
    from semabridge.repository.command_logger import get_command_logger, CommandType, ActionType
    ent_logger = get_enterprise_logger()
    cmd_logger = get_command_logger()
    
    ent_logger.start_session("sync")
    log_entry = cmd_logger.log_start(
        command=CommandType.DEPLOY,
        action_type=ActionType.DEPLOYMENT,
        details={"dry_run": dry_run}
    )
    
    start_time = time.time()
    try:
        settings = get_settings()
    
        # Load config to backfill missing parameters
        from semabridge.core.config_loader import get_default_config_path, load_yaml_file
        config_path = get_default_config_path()
        
        if config_path:
            try:
                config = load_yaml_file(config_path)
                
                # If source not provided in CLI, try to get from config
                if not source and "source" in config and isinstance(config["source"], dict) and "type" in config["source"]:
                    source = config["source"]["type"]
                    console.print(f"[dim]Inferred source from config: {source}[/dim]")
                    
                # Backfill dataset_id if missing and source is fabric
                if not dataset_id and "source" in config and isinstance(config["source"], dict) and "dataset_id" in config["source"]:
                   # Only use it if source is actually fabric (or inferred as fabric)
                   if source and source.lower() == "fabric":
                       dataset_id = config["source"]["dataset_id"]
                       console.print(f"[dim]Using dataset ID from config: {dataset_id}[/dim]")

                # Backfill target if missing
                if not target and "target" in config and isinstance(config["target"], dict) and "type" in config["target"]:
                    target = config["target"]["type"]
                    console.print(f"[dim]Inferred target from config: {target}[/dim]")
                
                # Allow model name override from config if not CLI specified
                if not name and "model_name" in config:
                    name = config["model_name"]
                
                # Backfill model pattern from config source.model if not CLI specified
                if not model and "source" in config and isinstance(config["source"], dict):
                    if "model" in config["source"]:
                        model = config["source"]["model"]
                        console.print(f"[dim]Using model pattern from config: {model}[/dim]")
                
                # Allow version tag override from config if not CLI specified
                if not tag and "version_tag" in config:
                    tag = str(config["version_tag"])
                    console.print(f"[dim]Using version tag from config: {tag}[/dim]")

            except Exception as e:
                console.print(f"[yellow]Warning: Failed to load config: {e}[/yellow]")
                        
        if not source:
            console.print("[red]Error: Source platform not specified and could not be found in config.[/red]")
            raise typer.Exit(code=1)

        source = source.lower()
        
        if target:
            target = target.lower()
        else:
            # Infer target
            if source == "snowflake":
                target = "fabric"
                console.print(f"[dim]Inferring target: {target}[/dim]")
            elif source == "fabric":
                target = "snowflake"
                console.print(f"[dim]Inferring target: {target}[/dim]")
            else:
                 console.print(f"[red]Error: Could not infer target for source '{source}'. Please specify target.[/red]")
                 raise typer.Exit(code=1)
        
        if source == "snowflake" and target == "fabric":
            import os
            if os.getenv("SEMABRIDGE_ORCHESTRATOR", "local").lower() == "temporal":
                import asyncio
                from semabridge.orchestration.orchestrator_adapter import run_sync
                res = asyncio.run(run_sync(
                    tenant_id=os.getenv("FABRIC_TENANT_ID", "default"),
                    model_id=name or settings.model.name,
                    database=settings.snowflake.database,
                    schema=settings.snowflake.schema_name,
                    snowflake_account=settings.snowflake.account,
                    fabric_workspace_id=settings.fabric.workspace_id,
                    warehouse_size="SMALL",
                    version_tag=tag if tag else "",
                    force_full_sync=False,
                    mode="temporal"
                ))
                if res.status == "completed":
                    console.print(f"[bold green]Temporal Sync Result: {res.status}[/bold green]")
                else:
                    console.print(f"[bold red]Temporal Sync Failed: {res.error}[/bold red]")
            else:
                _run_snowflake_to_fabric(settings, name, dry_run, output_dir, log_entry=log_entry, model_pattern=model)
        elif source == "fabric" and target == "snowflake":
            sync_to_snowflake = not dry_run
            
            # Resolve model_pattern and model_list from --model option or config
            model_pattern = None
            model_list = None
            if model:
                model_pattern = model
            
            if not dataset_id and not model_pattern:
                 console.print("[red]Error: Either --dataset-id or --model pattern is required when source is 'fabric'[/red]")
                 console.print("[dim]Tip: Use --model '*' to sync all models, or --model 'Sale*' for glob patterns[/dim]")
                 raise typer.Exit(code=1)
            _run_fabric_to_snowflake(settings, dataset_id, workspace_id, tag, sync_to_snowflake, log_entry=log_entry, model_pattern=model_pattern, model_list=model_list)
    except typer.Exit:
        raise
    except Exception as e:
        duration = int((time.time() - start_time) * 1000)
        cmd_logger.log_failure(log_entry, str(e), duration)
        raise
    finally:
        ent_logger.stop_session()


@app.command()
def compare(
    source: str = typer.Argument(..., help="Source environment (snowflake/fabric)"),
    target: str = typer.Argument(..., help="Target environment (snowflake/fabric)"),
    dataset_id: str = typer.Option(..., "--dataset-id", "-d", help="Fabric Dataset ID or Name"),
    format: str = typer.Option("cli", "--format", "-f", help="Output format: cli, json, html"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file for report"),
):
    """
    Compare semantic models (e.g. Source vs Repository).
    """
    show_banner()
    source = source.lower()
    target = target.lower()
    
    # 1. Resolve Dataset ID
    from semabridge.repository.duckdb_manager import DuckDBManager
    db_manager = DuckDBManager()
    conn = db_manager._get_connection()
    try:
        results = conn.execute("SELECT project_id, name FROM projects").fetchall()
    finally:
        conn.close()
    
    matched_id = dataset_id
    
    # Check exact ID match
    if any(r[0] == dataset_id for r in results):
        matched_id = dataset_id
    else:
        # Search by name or ID
        search = dataset_id.lower()
        matches = []
        for pid, name in results:
            # Check ID
            if search == pid.lower():
                matches = [(pid, name)]
                break
            # Check Name
            if name and name.lower() == search:
                matches = [(pid, name)]
                break
            
            # Substrings
            match_id = search in pid.lower()
            match_name = name and search in name.lower()
            
            if match_id or match_name:
                matches.append((pid, name))
        
        if len(matches) == 1:
            matched_id = matches[0][0]
            console.print(f"[dim]Resolved project '{dataset_id}' to ID: {matched_id} ({matches[0][1]})[/dim]")
        elif len(matches) > 1:
            console.print(f"[red]Ambiguous project name '{dataset_id}'. Matches:[/red]")
            for m in matches:
                console.print(f"  - {m[1]} ({m[0]})")
            raise typer.Exit(code=1)
        else:
             console.print(f"[red]Project '{dataset_id}' not found.[/red]")
             raise typer.Exit(code=1)

    # 2. Invoke Diff
    from semabridge.cli.diff_commands import diff_source
    
    if source in ["snowflake", "fabric"]:
         diff_source(dataset_id=matched_id, source=source, format=format, output=output)
    else:
        console.print(f"[red]Unsupported source: {source}[/red]")
        raise typer.Exit(code=1)


@app.command("parallel-sync")
def parallel_sync(
    source: Optional[str] = typer.Argument(None, help="Source platform (snowflake/fabric). Optional if defined in config."),
    target: Optional[str] = typer.Argument(None, help="Target platform. Inferred if omitted."),
    workers: int = typer.Option(0, "--workers", "-w", help="Max parallel workers (0=auto, based on CPU count)"),
    strict: bool = typer.Option(False, "--strict", help="Stop all workers on first failure"),
    no_parallel: bool = typer.Option(False, "--no-parallel", help="Disable parallelism (sequential fallback)"),
    resume: Optional[str] = typer.Option(None, "--resume", help="Resume failed models from a previous batch ID"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model name pattern (glob: *, Sale*, *_Prod)"),
    dataset_id: Optional[str] = typer.Option(None, "--dataset-id", "-d", help="Fabric Dataset ID"),
    workspace_id: Optional[str] = typer.Option(None, "--workspace-id", help="Fabric Workspace ID"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Version tag"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Generate artifacts without deploying"),
    max_retries: int = typer.Option(3, "--max-retries", help="Max retries for transient errors"),
    no_dashboard: bool = typer.Option(False, "--no-dashboard", help="Disable the live progress dashboard"),
):
    """
    Synchronize multiple semantic models in parallel using ProcessPoolExecutor.

    Each model is processed in an isolated worker process through the full
    10-step pipeline (extract → convert → deploy) with retry logic and
    graceful error handling.

    Usage:
      semabridge parallel-sync snowflake              # All models, auto workers
      semabridge parallel-sync fabric --workers 4     # 4 workers
      semabridge parallel-sync fabric --strict        # Stop on first failure
      semabridge parallel-sync fabric --resume abc123 # Resume failed models
      semabridge parallel-sync fabric --no-parallel   # Sequential fallback
    """
    show_banner()

    from semabridge.core.concurrency.models import ConcurrencyConfig, ExecutionMode, RetryConfig
    from semabridge.core.concurrency.orchestrator import ConcurrencyOrchestrator
    from semabridge.core.concurrency.error_reporter import ErrorReporter
    from semabridge.repository.duckdb_manager import DuckDBManager

    start_time = time.time()

    try:
        settings = get_settings()

        # Load config for backfilling
        from semabridge.core.config_loader import get_default_config_path, load_yaml_file
        from semabridge.core.project import ProjectConfig, load_project_config

        config_path = get_default_config_path()
        project_config = None

        if config_path:
            try:
                project_config = load_project_config(config_path)

                if not source and project_config.source:
                    source = project_config.source.type.value
                    console.print(f"[dim]Inferred source from config: {source}[/dim]")

                if not target and project_config.targets:
                    target = project_config.targets[0].type.value
                    console.print(f"[dim]Inferred target from config: {target}[/dim]")

                if not model and project_config.source.model != "*":
                    model = project_config.source.model

            except Exception as e:
                console.print(f"[yellow]Warning: Failed to load config: {e}[/yellow]")

        if not source:
            console.print("[red]Error: Source platform not specified and could not be found in config.[/red]")
            raise typer.Exit(code=1)

        source = source.lower()
        if target:
            target = target.lower()
        else:
            target = "fabric" if source == "snowflake" else "snowflake"
            console.print(f"[dim]Inferring target: {target}[/dim]")

        # Build concurrency configuration
        mode = ExecutionMode.STRICT if strict else ExecutionMode.BEST_EFFORT
        if no_parallel:
            workers = 1

        cc = ConcurrencyConfig(
            max_processes=workers if workers > 0 else None,
            enable_parallel=not no_parallel,
        )
        retry_cfg = RetryConfig(max_retries=max_retries)

        # Build a minimal ProjectConfig if not loaded from file
        if project_config is None:
            from semabridge.core.project import SourceConfig, SourceType
            project_config = ProjectConfig(
                source=SourceConfig(
                    type=SourceType(source),
                    model=model or "*",
                    database="default" if source == "snowflake" else None,
                    workspace="default" if source == "fabric" else None,
                ),
            )

        db_manager = DuckDBManager()

        orchestrator = ConcurrencyOrchestrator(
            config=project_config,
            concurrency_config=cc,
            retry_config=retry_cfg,
            source=source,
            target=target,
            config_path=str(config_path) if config_path else None,
            deploy=not dry_run,
            tag=tag,
            dry_run=dry_run,
            dataset_id=dataset_id,
            workspace_id=workspace_id,
            mode=mode,
            show_dashboard=not no_dashboard,
            db_manager=db_manager,
        )

        # Run or resume
        import os
        if os.getenv("SEMABRIDGE_ORCHESTRATOR", "local").lower() == "temporal":
            console.print("[bold cyan]Delegating parallel sync to Temporal (Docker)...[/bold cyan]")
            from semabridge.core.engine import SemaBridgeEngine
            import asyncio
            engine = SemaBridgeEngine(project_config, duckdb_manager=db_manager)
            try:
                loop = asyncio.get_running_loop()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    engine_result = pool.submit(lambda: asyncio.run(engine._execute_temporal())).result()
            except RuntimeError:
                engine_result = asyncio.run(engine._execute_temporal())
                
            duration = time.time() - start_time
            if engine_result.success:
                console.print(f"[bold green]All {engine_result.models_processed} models synced in {duration:.1f}s via Temporal[/bold green]")
            else:
                console.print(f"[bold red]Sync via Temporal failed with {engine_result.targets_failed} errors:[/bold red]")
                for err in engine_result.errors:
                    console.print(f"  - {err}")
                raise typer.Exit(code=1)
            return

        if resume:
            console.print(f"[bold]Resuming batch {resume}…[/bold]")
            batch = orchestrator.resume(resume)
        else:
            batch = orchestrator.run()

        # Report
        reporter = ErrorReporter()
        reporter.log_summary(batch)

        if batch.failed_models:
            reporter.generate_json_report(batch)
            reporter.generate_markdown_report(batch)
            console.print(f"\n[yellow]Resume failed models with:[/yellow]")
            console.print(f"  semabridge parallel-sync {source} --resume {batch.batch_id}\n")

        duration = time.time() - start_time
        if batch.all_succeeded:
            console.print(f"[bold green]All {batch.total_models} models synced in {duration:.1f}s[/bold green]")
        else:
            console.print(
                f"[bold yellow]{batch.success_count}/{batch.total_models} succeeded, "
                f"{batch.failure_count} failed in {duration:.1f}s[/bold yellow]"
            )
            raise typer.Exit(code=1)

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        raise typer.Exit(code=1)


def main() -> None:
    """Execute the Semabridge CLI application."""
    app()


if __name__ == "__main__":
    main()


