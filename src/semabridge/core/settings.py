"""
Configuration settings for Semabridge.

Uses Pydantic Settings for type-safe configuration loading from environment
variables and .env files. Avoids the issues from semantic-sync by:
1. Clear separation of config concerns (Snowflake, Fabric, Model)
2. No nested Settings objects that cause attribute errors
3. Explicit validation with helpful error messages
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SnowflakeConfig(BaseSettings):
    """Snowflake connection configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="SNOWFLAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    account: str = Field(..., description="Snowflake account identifier (e.g., abc123.us-east-1)")
    user: str = Field(..., description="Snowflake username")
    password: SecretStr = Field(..., description="Snowflake password")
    warehouse: str = Field(..., description="Snowflake warehouse name")
    database: str = Field(..., description="Snowflake database name")
    schema_name: str = Field(default="PUBLIC", validation_alias="SNOWFLAKE_SCHEMA", description="Snowflake schema name")
    role: Optional[str] = Field(default=None, description="Snowflake role (optional)")
    
    @field_validator("account")
    @classmethod
    def validate_account(cls, v: str) -> str:
        """Ensure account identifier is properly formatted."""
        if not v or v == "your-account.region":
            raise ValueError("SNOWFLAKE_ACCOUNT must be set to your actual Snowflake account")
        return v.strip()


class FabricConfig(BaseSettings):
    """Microsoft Fabric configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="FABRIC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    tenant_id: str = Field(..., description="Azure AD tenant ID")
    client_id: str = Field(..., description="Azure AD application (client) ID")
    client_secret: SecretStr = Field(..., description="Azure AD client secret")
    workspace_id: str = Field(..., description="Fabric workspace ID")
    
    # API endpoints
    api_base_url: str = Field(
        default="https://api.fabric.microsoft.com/v1",
        description="Fabric REST API base URL"
    )
    power_bi_api_url: str = Field(
        default="https://api.powerbi.com/v1.0/myorg",
        description="Power BI REST API base URL"
    )
    
    # Timeout configuration (seconds)
    connect_timeout: int = Field(
        default=30,
        description="TCP connect timeout in seconds for Fabric API calls"
    )
    read_timeout: int = Field(
        default=120,
        description="Read timeout in seconds for Fabric API calls"
    )
    
    @field_validator("tenant_id", "client_id", "workspace_id")
    @classmethod
    def validate_guid(cls, v: str, info) -> str:
        """Validate GUID format."""
        if not v or v.startswith("your-"):
            raise ValueError(f"FABRIC_{info.field_name.upper()} must be set to a valid GUID")
        return v.strip()


class ModelConfig(BaseSettings):
    """Semantic model configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="MODEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    name: str = Field(
        default="SnowflakeSemanticModel",
        description="Name of the semantic model to create"
    )
    description: str = Field(
        default="Auto-generated semantic model from Snowflake metadata",
        description="Description of the semantic model"
    )
    exclude_tables: str = Field(
        default="_SEMANTIC_METADATA,_SEMANTIC_MEASURES,_SEMANTIC_RELATIONSHIPS,_SEMANTIC_SYNC_HISTORY,_SEMANTIC_COLUMNS",
        description="Comma-separated list of tables to exclude"
    )
    include_tables: Optional[str] = Field(
        default=None,
        description="Comma-separated list of tables to include (if set, only these tables are processed)"
    )
    
    # Performance settings
    cache_enabled: bool = Field(
        default=True,
        description="Enable incremental processing cache"
    )
    cache_dir: str = Field(
        default=".semabridge_cache",
        description="Directory for cache files"
    )
    
    @property
    def included_table_list(self) -> list[str] | None:
        """Get list of included tables (None means all)."""
        if not self.include_tables:
            return None
        return [t.strip().upper() for t in self.include_tables.split(",") if t.strip()]

    @property
    def excluded_table_list(self) -> list[str]:
        """Get list of excluded tables."""
        if not self.exclude_tables:
            return []
        return [t.strip().upper() for t in self.exclude_tables.split(",") if t.strip()]


class LoggingConfig(BaseSettings):
    """Logging configuration."""

    model_config = SettingsConfigDict(
        env_prefix="LOGGING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    level: str = Field(default="INFO", description="Logging level")
    format: Optional[str] = Field(
        default="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        description="Log format string"
    )
    dir: Optional[str] = Field(default=None, description="Directory for log files")

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        """Validate logging level."""
        allowed = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in allowed:
            raise ValueError(f"Invalid log level '{v}'. Allowed values: {', '.join(allowed)}")
        return v.upper()


class CoreConfig(BaseSettings):
    """Core application settings."""

    model_config = SettingsConfigDict(
        env_prefix="CORE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    repository_path: str = Field(
        default="~/.semabridge/semabridge.db",
        description="Path to the DuckDB repository"
    )
    default_intermediary: str = Field(
        default="osi",
        description="Default intermediate format (osi/sml)"
    )
    ui_font_size: int = Field(
        default=10,
        description="Global UI font size in points"
    )


class Settings(BaseSettings):
    """
    Combined settings for the entire application.
    
    Unlike semantic-sync, we use composition with explicit loading
    to avoid attribute access issues.
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # Sub-configurations are loaded separately to avoid nesting issues
    _snowflake: Optional[SnowflakeConfig] = None
    _fabric: Optional[FabricConfig] = None
    _model: Optional[ModelConfig] = None
    _logging: Optional[LoggingConfig] = None
    _core: Optional[CoreConfig] = None
    _llm: Optional[LLMConfig] = None

    @property
    def snowflake(self) -> SnowflakeConfig:
        """Get Snowflake configuration (lazy loaded)."""
        if self._snowflake is None:
            self._snowflake = SnowflakeConfig()
        return self._snowflake
    
    @property
    def fabric(self) -> FabricConfig:
        """Get Fabric configuration (lazy loaded)."""
        if self._fabric is None:
            self._fabric = FabricConfig()
        return self._fabric
    
    @property
    def model(self) -> ModelConfig:
        """Get model configuration (lazy loaded)."""
        if self._model is None:
            self._model = ModelConfig()
        return self._model
    
    @property
    def logging(self) -> LoggingConfig:
        """Get logging configuration (lazy loaded)."""
        if self._logging is None:
            self._logging = LoggingConfig()
        return self._logging
    
    @property
    def core(self) -> CoreConfig:
        """Get core configuration (lazy loaded)."""
        if self._core is None:
            self._core = CoreConfig()
        return self._core

    @property
    def llm(self) -> LLMConfig:
        """Get LLM configuration (lazy loaded)."""
        if self._llm is None:
            self._llm = LLMConfig()
        return self._llm
    
    def validate_snowflake(self) -> bool:
        """Validate Snowflake configuration is complete."""
        try:
            _ = self.snowflake
            return True
        except Exception:
            return False
    
    def validate_fabric(self) -> bool:
        """Validate Fabric configuration is complete."""
        try:
            _ = self.fabric
            return True
        except Exception:
            return False


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached application settings.
    
    Uses lru_cache to ensure settings are loaded only once.
    To reload settings, call get_settings.cache_clear().
    """
    # Ensure we're looking for .env in the right place
    env_file = Path.cwd() / ".env"
    
    # Try parent directories for .env, but DO NOT change CWD
    # Pydantic Settings will find .env by default in CWD, but if it's missing,
    # we explicitly point to it if found in parent
    
    # Load from environment variables first
    config = Settings()
    
    if not env_file.exists():
        for parent in Path.cwd().parents:
            candidate = parent / ".env"
            if candidate.exists():
                config = Settings(_env_file=candidate)
                break
    
    # Merge with YAML configuration if present
    try:
        from semabridge.core.config_loader import get_default_config_path, load_and_merge_configs
        
        config_paths = []
        
        # 1. Global config (system-wide)
        global_path = Path.home() / ".semabridge" / "semabridge.yaml"
        if global_path.exists():
            config_paths.append(global_path)
            
        # 2. Local config (project-specific)
        local_path = get_default_config_path()
        if local_path:
            config_paths.append(local_path)
            
        if config_paths:
            yaml_data, _ = load_and_merge_configs(config_paths, resolve_env=True)
            
            # Update sub-configs with YAML data
            if "snowflake" in yaml_data:
                config._snowflake = SnowflakeConfig(**yaml_data["snowflake"])
            if "fabric" in yaml_data:
                config._fabric = FabricConfig(**yaml_data["fabric"])
            if "model" in yaml_data:
                config._model = ModelConfig(**yaml_data["model"])
            if "logging" in yaml_data:
                config._logging = LoggingConfig(**yaml_data["logging"])
            if "core" in yaml_data:
                config._core = CoreConfig(**yaml_data["core"])
            if "llm" in yaml_data:
                config._llm = LLMConfig(**yaml_data["llm"])
                
    except Exception as e:
        # Don't fail if config is missing or invalid, just use defaults/env
        # But log it if possible, or just pass as before
        pass
                
    return config


class LLMConfig(BaseSettings):
    """Configuration for LLM-based remediation."""
    
    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    provider: str = Field(default="openai", description="LLM provider (openai, azure)")
    api_key: Optional[SecretStr] = Field(default=None, description="API Key")
    base_url: Optional[str] = Field(default=None, description="Base URL for API")
    model_name: str = Field(default="gpt-4", description="Model deployment name")
    
    @property
    def is_configured(self) -> bool:
        """Check if LLM is adequately configured."""
        return self.api_key is not None



def reload_settings() -> Settings:
    """Force reload of settings (clears cache)."""
    get_settings.cache_clear()
    return get_settings()
