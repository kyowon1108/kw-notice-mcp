"""Value types shared by repository-local source checks."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuleViolation:
    """One source-level strictness violation."""

    path: Path
    line: int
    rule: str
    message: str
