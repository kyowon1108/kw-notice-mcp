"""Typed contracts for failure-safe Release generations."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Final, Literal, Protocol, Self, final, override

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RestoreObservation(StrEnum):
    """Remote states that control whether refresh may continue."""

    ABSENT = "absent"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    API_FAILURE = "api_failure"
    DOWNLOAD_FAILURE = "download_failure"
    CHECKSUM_MISMATCH = "checksum_mismatch"


class RestoreAction(StrEnum):
    """Safe local action selected from one remote observation."""

    INITIALIZE = "initialize"
    RESTORE = "restore"
    RETAIN = "retain"


@dataclass(frozen=True, slots=True)
class RefreshDecision:
    """Whether restore handling permits crawl and later publication."""

    action: RestoreAction
    crawl: bool
    publish: bool


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    """Whether a CLI result is allowed to reach Release publication."""

    publish: bool
    reason: str


class RefreshEffects(Protocol):
    """Side-effect boundary used by executable offline workflow simulations."""

    def initialize(self) -> None:
        """Initialize only when the remote protocol is truly absent."""
        ...

    def restore(self) -> None:
        """Install one verified prior generation."""
        ...

    def crawl(self) -> int:
        """Run one metadata-only crawl and return its CLI exit code."""
        ...

    def publish(self) -> None:
        """Publish generation assets and then the pointer."""
        ...


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    """Committed local generation paths and digest."""

    database: Path
    checksum: Path
    pointer: Path
    generation_dir: Path
    sha256: str


class ReleaseManifest(BaseModel):
    """Last-published pointer to one immutable SHA-addressed generation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    database_asset: str
    checksum_asset: str

    @model_validator(mode="after")
    def exact_generation_names(self) -> Self:
        """Reject traversal and names that are not derived from the digest."""
        expected_database = f"notices-{self.sha256}.sqlite3"
        expected_checksum = f"{expected_database}.sha256"
        if self.database_asset != expected_database:
            reason = "database_asset"
            raise ValueError(reason)
        if self.checksum_asset != expected_checksum:
            reason = "checksum_asset"
            raise ValueError(reason)
        return self


class ReleasePointer(BaseModel):
    """Strict GitHub Release body pointing at one immutable manifest asset."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    manifest_asset: str = Field(pattern=r"^notices-manifest-[0-9a-f]{64}\.json$")


@final
class SnapshotError(Exception):
    """A local or downloaded generation cannot be accepted safely."""

    reason: str

    def __init__(self, reason: str) -> None:
        """Store only a bounded reason token."""
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        """Return a non-sensitive diagnostic."""
        return f"invalid release snapshot: {self.reason}"


_REFRESH_DECISIONS: Final[dict[RestoreObservation, RefreshDecision]] = {
    RestoreObservation.ABSENT: RefreshDecision(
        RestoreAction.INITIALIZE, crawl=True, publish=False
    ),
    RestoreObservation.COMPLETE: RefreshDecision(
        RestoreAction.RESTORE, crawl=True, publish=False
    ),
    RestoreObservation.INCOMPLETE: RefreshDecision(
        RestoreAction.RETAIN, crawl=False, publish=False
    ),
    RestoreObservation.API_FAILURE: RefreshDecision(
        RestoreAction.RETAIN, crawl=False, publish=False
    ),
    RestoreObservation.DOWNLOAD_FAILURE: RefreshDecision(
        RestoreAction.RETAIN, crawl=False, publish=False
    ),
    RestoreObservation.CHECKSUM_MISMATCH: RefreshDecision(
        RestoreAction.RETAIN, crawl=False, publish=False
    ),
}


def refresh_decision(observation: RestoreObservation) -> RefreshDecision:
    """Stop before crawl/publication for every non-absent remote failure."""
    return _REFRESH_DECISIONS[observation]


def publication_decision(exit_code: int) -> PublicationDecision:
    """Permit pointer publication only for the CLI's exact success exit code."""
    if exit_code == 0:
        return PublicationDecision(publish=True, reason="crawl_success")
    if exit_code in {10, 11, 12, 13}:
        return PublicationDecision(publish=False, reason="crawl_failed")
    return PublicationDecision(publish=False, reason="unknown_exit")


def restore_decision(release_exists: bool, download_succeeded: bool) -> RestoreAction:
    """Preserve the original two-input decision API for existing callers."""
    if not release_exists:
        observation = RestoreObservation.ABSENT
    elif download_succeeded:
        observation = RestoreObservation.COMPLETE
    else:
        observation = RestoreObservation.DOWNLOAD_FAILURE
    return refresh_decision(observation).action


def execute_refresh(observation: RestoreObservation, effects: RefreshEffects) -> None:
    """Execute the refresh gate while keeping remote effects injectable in tests."""
    decision = refresh_decision(observation)
    if not decision.crawl:
        return
    actions = {
        RestoreAction.INITIALIZE: effects.initialize,
        RestoreAction.RESTORE: effects.restore,
    }
    action = actions.get(decision.action)
    if action is None:
        return
    action()
    if publication_decision(effects.crawl()).publish:
        effects.publish()
