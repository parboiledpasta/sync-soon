"""
Logging utilities for Semabridge.

Provides consistent logging across the application with Rich formatting.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler

# Console for rich output
console = Console()

# Logger cache
_loggers: dict[str, logging.Logger] = {}


def setup_logging(
    level: str = "INFO",
    format_string: Optional[str] = None,
    rich_output: bool = True,
    log_dir: Optional[str] = None,
) -> None:
    """
    Set up logging for the application.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_string: Optional format string for log messages
        rich_output: Whether to use Rich for formatted output
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    # Clear existing handlers
    root = logging.getLogger()
    root.handlers.clear()
    
    if rich_output:
        # Use Rich handler for beautiful console output
        handler = RichHandler(
            console=console,
            show_time=True,
            show_path=False,
            rich_tracebacks=True,
            tracebacks_show_locals=False,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
    else:
        # Standard handler
        handler = logging.StreamHandler(sys.stdout)
        fmt = format_string or "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        handler.setFormatter(logging.Formatter(fmt))
    
    handler.setLevel(log_level)
    root.addHandler(handler)
    root.setLevel(log_level)
    
    # Reduce noise from third-party libraries
    logging.getLogger("snowflake.connector").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("msal").setLevel(logging.WARNING)
    
    # Initialize Enterprise Logger
    from semabridge.utils.enterprise_logger import get_enterprise_logger
    
    # If explicit log dir provided, initialize with it
    if log_dir:
        get_enterprise_logger(logs_dir=log_dir)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Configured logger instance
    """
    if name not in _loggers:
        logger = logging.getLogger(name)
        _loggers[name] = logger
    return _loggers[name]
