"""Typed decisions for the repository bootstrap preflight."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

EXPECTED_ORIGIN: Final = "https://github.com/kyowon1108/kw-notice-mcp.git"


class PreflightCode(StrEnum):
    """Machine-readable outcomes for the pre-write gate."""

    ALLOWED = "allowed"
    BLOCKED_EXISTING_GIT = "existing_git"
    BLOCKED_WRONG_ORIGIN = "wrong_origin"
    BLOCKED_NONEMPTY_REMOTE = "nonempty_remote"
    BLOCKED_REMOTE_LOOKUP_FAILURE = "remote_lookup_failure"
    BLOCKED_REMOTE_OUTPUT = "remote_output"


@dataclass(frozen=True, slots=True)
class RemoteProbe:
    """Captured result of the exact remote emptiness command."""

    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class PreflightInput:
    """Inputs observed without mutating the workspace."""

    workspace_has_git: bool
    configured_origin: str | None
    remote: RemoteProbe


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """A preflight result that explicitly records write permission."""

    code: PreflightCode
    detail: str

    @property
    def project_file_write_allowed(self) -> bool:
        """Return whether product files may be created."""
        return self.code is PreflightCode.ALLOWED


class ProjectWriter(Protocol):
    """Capability for creating the project files after a clean preflight."""

    def write_project_files(self) -> None:
        """Create the project files owned by the bootstrap operation."""


def evaluate_preflight(inputs: PreflightInput) -> PreflightResult:
    """Evaluate every pre-write condition in deterministic priority order."""
    code = PreflightCode.ALLOWED
    detail = "preflight conditions are clean"
    if inputs.workspace_has_git:
        code = PreflightCode.BLOCKED_EXISTING_GIT
        detail = "local .git exists"
    elif inputs.configured_origin not in (None, EXPECTED_ORIGIN):
        code = PreflightCode.BLOCKED_WRONG_ORIGIN
        detail = "configured origin is not the requested HTTPS URL"
    elif inputs.remote.exit_code != 0:
        code = PreflightCode.BLOCKED_REMOTE_LOOKUP_FAILURE
        detail = "exact remote lookup did not exit successfully"
    elif inputs.remote.stdout:
        if "refs/" in inputs.remote.stdout:
            code = PreflightCode.BLOCKED_NONEMPTY_REMOTE
            detail = "exact remote lookup returned refs"
        else:
            code = PreflightCode.BLOCKED_REMOTE_OUTPUT
            detail = "exact remote lookup returned unexpected stdout"
    elif inputs.remote.stderr:
        code = PreflightCode.BLOCKED_REMOTE_OUTPUT
        detail = "exact remote lookup returned unexpected stderr"
    return PreflightResult(code, detail)


def bootstrap_project(
    inputs: PreflightInput,
    writer: ProjectWriter,
) -> PreflightResult:
    """Run preflight and create project files only when it permits writes."""
    result = evaluate_preflight(inputs)
    if result.project_file_write_allowed:
        writer.write_project_files()
    return result
