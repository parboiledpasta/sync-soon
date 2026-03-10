"""
SemaBridge Custom Exceptions.

This module defines the exception hierarchy for SemaBridge.
All exceptions inherit from SemaBridgeError to enable unified error handling.

Usage:
    from semabridge.core.exceptions import ConnectorError, MissingCredentialError

    if not os.environ.get("SNOWFLAKE_PASSWORD_ENV"):
        raise MissingCredentialError("SNOWFLAKE_PASSWORD_ENV")
"""

from typing import Any, Dict, List, Optional


class SemaBridgeError(Exception):
    """
    Base exception for all SemaBridge errors.

    All custom exceptions inherit from this class to enable unified
    error handling and consistent error message formatting.

    Args:
        message: Human-readable error description.
        details: Optional dictionary with additional context.
    """

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        """
        Initialize base exception.

        Args:
            message: Human-readable error description.
            details: Optional dictionary with additional context.
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        """Return formatted error message."""
        if self.details:
            detail_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{self.message} ({detail_str})"
        return self.message


class ConnectorError(SemaBridgeError):
    """
    Error during connector operations.

    Raised when a connector fails to connect, authenticate,
    or perform operations on the target system.

    Args:
        message: Error description.
        connector_name: Name of the connector that failed.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        connector_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize connector error.

        Args:
            message: Error description.
            connector_name: Name of the connector that failed.
            details: Optional additional context.
        """
        full_details = details or {}
        if connector_name:
            full_details["connector"] = connector_name
        super().__init__(message, full_details)
        self.connector_name = connector_name


class MissingCredentialError(ConnectorError):
    """
    Required environment variable is not set.

    Raised during authentication when a required credential
    environment variable is missing or empty.

    Args:
        env_var_name: Name of the missing environment variable.
        connector_name: Optional connector that requires this credential.
    """

    def __init__(
        self,
        env_var_name: str,
        connector_name: Optional[str] = None,
    ):
        """
        Initialize missing credential error.

        Args:
            env_var_name: Name of the missing environment variable.
            connector_name: Optional connector that requires this credential.
        """
        message = f"Required environment variable '{env_var_name}' is not set"
        super().__init__(
            message,
            connector_name=connector_name,
            details={"env_var": env_var_name},
        )
        self.env_var_name = env_var_name


class ConversionError(SemaBridgeError):
    """
    Error during format conversion.

    Raised when converting between formats (e.g., TMSL to SML,
    SML to Snowflake) fails due to incompatible data or logic errors.

    Args:
        message: Error description.
        source_format: Source format being converted from.
        target_format: Target format being converted to.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        source_format: Optional[str] = None,
        target_format: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize conversion error.

        Args:
            message: Error description.
            source_format: Source format being converted from.
            target_format: Target format being converted to.
            details: Optional additional context.
        """
        full_details = details or {}
        if source_format:
            full_details["source_format"] = source_format
        if target_format:
            full_details["target_format"] = target_format
        super().__init__(message, full_details)
        self.source_format = source_format
        self.target_format = target_format


class ValidationError(SemaBridgeError):
    """
    Schema or semantic validation failed.

    Raised when a model or configuration fails validation checks,
    including Pydantic schema validation and semantic integrity checks.

    Args:
        message: Error description.
        field: Optional field name that failed validation.
        line_number: Optional YAML line number where error occurred.
        errors: Optional list of validation error messages.
    """

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        line_number: Optional[int] = None,
        errors: Optional[List[str]] = None,
    ):
        """
        Initialize validation error.

        Args:
            message: Error description.
            field: Optional field name that failed validation.
            line_number: Optional YAML line number where error occurred.
            errors: Optional list of validation error messages.
        """
        details: Dict[str, Any] = {}
        if field:
            details["field"] = field
        if line_number:
            details["line"] = line_number
        if errors:
            details["error_count"] = len(errors)
        super().__init__(message, details)
        self.field = field
        self.line_number = line_number
        self.errors = errors or []


class RepositoryError(SemaBridgeError):
    """
    Error during repository operations.

    Raised when DuckDB repository operations fail, including
    versioning, migration, or rollback operations.

    Args:
        message: Error description.
        operation: Type of operation that failed.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        operation: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize repository error.

        Args:
            message: Error description.
            operation: Type of operation that failed.
            details: Optional additional context.
        """
        full_details = details or {}
        if operation:
            full_details["operation"] = operation
        super().__init__(message, full_details)
        self.operation = operation


class PluginError(SemaBridgeError):
    """
    Error during plugin operations.

    Raised when plugin loading, initialization, or execution fails.

    Args:
        message: Error description.
        plugin_name: Name of the plugin that failed.
        details: Optional additional context.
    """

    def __init__(
        self,
        message: str,
        plugin_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize plugin error.

        Args:
            message: Error description.
            plugin_name: Name of the plugin that failed.
            details: Optional additional context.
        """
        full_details = details or {}
        if plugin_name:
            full_details["plugin"] = plugin_name
        super().__init__(message, full_details)
        self.plugin_name = plugin_name
