"""Immutable collector configuration and result contracts."""

from dataclasses import dataclass
from enum import StrEnum

MAX_DURATION_SECONDS = 10 * 60
MAX_DETAILS = 100


class CollectStatus(StrEnum):
    """Stable collector outcomes consumed by operational callers."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    BUDGET_EXCEEDED = "budget_exceeded"
    BUSY = "busy"


class CollectionMode(StrEnum):
    """Operational depth selected by an internal caller."""

    FULL = "full"
    METADATA_ONLY = "metadata_only"


@dataclass(frozen=True, slots=True)
class CollectorConfig:
    """Hard-bounded options with no policy or robots override."""

    max_pages: int = 1
    max_detail_requests: int = MAX_DETAILS
    max_duration_seconds: float = MAX_DURATION_SECONDS
    user_agent: str = "kw-notice-mcp/0.1 (+local metadata collector)"
    mode: CollectionMode = CollectionMode.FULL


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """Safe run outcome with counters and no response content."""

    status: CollectStatus
    run_id: str
    reason: str | None
    wire_requests: int
    retry_count: int
