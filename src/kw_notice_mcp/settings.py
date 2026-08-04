"""Validated environment and CLI settings for local operations."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, TypeGuard, override

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kw_notice_mcp.collector_models import CollectionMode, CollectorConfig

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


@dataclass(frozen=True, slots=True)
class SettingsOverrides:
    """Typed command-line values that override environment settings."""

    db_path: Path | None = None
    log_level: str | None = None
    user_agent: str | None = None
    max_pages: int | None = None
    max_detail_requests: int | None = None
    max_duration_seconds: float | None = None


class InvalidConfigurationError(Exception):
    """A setting or local database path is outside the safe CLI contract."""

    parameter: str

    def __init__(self, parameter: str) -> None:
        """Store only the parameter name, never the supplied value."""
        super().__init__(parameter)
        self.parameter = parameter

    @override
    def __str__(self) -> str:
        """Return a safe configuration diagnostic."""
        return f"invalid configuration: {self.parameter}"


class Settings(BaseSettings):
    """Pydantic settings loaded from ``KW_NOTICE_`` environment variables."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="KW_NOTICE_",
        case_sensitive=False,
        extra="ignore",
        validate_assignment=True,
    )

    db_path: Path = Path("data/kw-notice.sqlite3")
    log_level: LogLevel = "INFO"
    user_agent: str = "kw-notice-mcp/0.1 (+local metadata collector)"
    max_pages: int = Field(default=1, ge=1, le=50)
    max_detail_requests: int = Field(default=100, ge=1, le=100)
    max_duration_seconds: float = Field(default=600.0, ge=1.0, le=600.0)

    @field_validator("user_agent")
    @classmethod
    def valid_user_agent(cls, value: str) -> str:
        """Require a bounded visible identification string."""
        min_length = 8
        max_length = 200
        if not min_length <= len(value) <= max_length or any(
            char.isspace() and char != " " for char in value
        ):
            reason = "user_agent"
            raise ValueError(reason)
        return value

    def collector_config(self) -> CollectorConfig:
        """Convert validated settings into the collector's immutable contract."""
        return CollectorConfig(
            max_pages=self.max_pages,
            max_detail_requests=self.max_detail_requests,
            max_duration_seconds=self.max_duration_seconds,
            user_agent=self.user_agent,
            mode=CollectionMode.METADATA_ONLY,
        )


class StatusSnapshot(BaseModel):
    """Small typed status projection used by the human CLI."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    database: str
    fts5: bool
    notices: int
    freshness: dict[str, int]
    crawl: str
    run_id: str | None
    pages: int
    details: int
    block_reason: str | None


def validate_database_path(path: Path) -> None:
    """Reject directory and symlink targets before SQLite resolves them."""
    if not str(path) or path.name in {"", ".", ".."}:
        reason = "db_path"
        raise InvalidConfigurationError(reason)
    if path.is_symlink():
        reason = "db_path_symlink"
        raise InvalidConfigurationError(reason)
    parent = path.parent
    if parent.exists() and not parent.is_dir():
        reason = "db_parent"
        raise InvalidConfigurationError(reason)
    if path.exists() and path.is_dir():
        reason = "db_path_directory"
        raise InvalidConfigurationError(reason)


def load_settings(overrides: SettingsOverrides) -> Settings:
    """Load settings with command-line values taking precedence over the env."""
    try:
        settings = Settings()
        if overrides.db_path is not None:
            settings.db_path = overrides.db_path
        if overrides.log_level is not None:
            if not is_log_level(overrides.log_level):
                reason = "log_level"
                raise InvalidConfigurationError(reason)
            settings.log_level = overrides.log_level
        if overrides.user_agent is not None:
            settings.user_agent = overrides.user_agent
        if overrides.max_pages is not None:
            settings.max_pages = overrides.max_pages
        if overrides.max_detail_requests is not None:
            settings.max_detail_requests = overrides.max_detail_requests
        if overrides.max_duration_seconds is not None:
            settings.max_duration_seconds = overrides.max_duration_seconds
    except (TypeError, ValueError) as error:
        reason = "settings"
        raise InvalidConfigurationError(reason) from error
    validate_database_path(settings.db_path)
    return settings


def is_log_level(value: str) -> TypeGuard[LogLevel]:
    """Narrow a command-line log level to the four supported names."""
    return value in {"DEBUG", "INFO", "WARNING", "ERROR"}
