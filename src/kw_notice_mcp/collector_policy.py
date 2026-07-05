"""Pure collector decisions shared by orchestration paths."""

from typing import TYPE_CHECKING

from kw_notice_mcp.collector_models import (
    MAX_DURATION_SECONDS,
    CollectionMode,
    CollectorConfig,
)
from kw_notice_mcp.robots import RobotsBlockReason, RobotsResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from kw_notice_mcp.domain import NoticeSummary
    from kw_notice_mcp.storage_models import StoredNotice


def needs_detail(summary: "NoticeSummary", existing: "StoredNotice | None") -> bool:
    """Return whether list metadata requires a fresh detail request."""
    if existing is None:
        return True
    return any(
        (
            existing.updated_date != summary.updated_date,
            existing.title != summary.title,
            existing.category_id != summary.category_id,
            existing.department != summary.department,
        )
    )


def time_exhausted(
    clock: "Callable[[], datetime]",
    started: "datetime",
    config: CollectorConfig,
) -> bool:
    """Return whether the injected wall clock reached the hard run cap."""
    elapsed = (clock() - started).total_seconds()
    return elapsed >= min(config.max_duration_seconds, MAX_DURATION_SECONDS)


def robots_missing_requires_full(policy: RobotsResult, mode: CollectionMode) -> bool:
    """Return whether a missing robots resource lacks metadata-only authorization."""
    return (
        policy.block_reason is RobotsBlockReason.ROBOTS_MISSING
        and mode is not CollectionMode.METADATA_ONLY
    )


def blocked_policy_reason(policy: RobotsResult) -> str:
    """Return the bounded reason for a robots policy that cannot proceed."""
    return (
        policy.block_reason.value
        if policy.block_reason is not None
        else RobotsBlockReason.MALFORMED.value
    )
