"""Typed failures raised by the SQLite storage boundary."""

from typing import final, override


class StorageError(Exception):
    """Base class for storage failures."""


@final
class StorageInputError(StorageError):
    """A caller supplied a value outside a bounded storage contract."""

    __slots__ = ("parameter", "value")
    parameter: str
    value: str

    def __init__(self, parameter: str, value: str) -> None:
        """Store the rejected parameter and bounded diagnostic value."""
        super().__init__(parameter, value)
        self.parameter = parameter
        self.value = value

    @override
    def __str__(self) -> str:
        """Describe the rejected parameter without executing it."""
        return f"invalid storage parameter: {self.parameter}={self.value!r}"


@final
class ReadOnlyStorageError(StorageError):
    """A write was attempted through a read-only connection."""

    __slots__ = ("operation",)
    operation: str

    def __init__(self, operation: str) -> None:
        """Store the rejected write operation."""
        super().__init__(operation)
        self.operation = operation

    @override
    def __str__(self) -> str:
        """Describe the rejected write operation."""
        return f"storage is read-only: {self.operation}"


@final
class FTS5UnavailableError(StorageError):
    """The SQLite runtime does not provide the required FTS5 module."""

    __slots__ = ()

    @override
    def __str__(self) -> str:
        """Describe the missing SQLite capability."""
        return "SQLite FTS5 is unavailable"


@final
class SchemaMigrationError(StorageError):
    """The database schema cannot be migrated safely."""

    __slots__ = ("version",)
    version: int

    def __init__(self, version: int) -> None:
        """Store the unsupported schema version."""
        super().__init__(version)
        self.version = version

    @override
    def __str__(self) -> str:
        """Describe the unsupported schema version."""
        return f"unsupported SQLite schema version: {self.version}"


@final
class StorageUnavailableError(StorageError):
    """The database could not be opened or queried."""

    __slots__ = ("reason",)
    reason: str

    def __init__(self, reason: str) -> None:
        """Store the safe infrastructure-failure reason."""
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        """Describe the infrastructure failure without raw database content."""
        return f"SQLite storage unavailable: {self.reason}"


@final
class CrawlBusyError(StorageError):
    """A non-stale crawl already owns the database run lease."""

    __slots__ = ("run_id",)
    run_id: str

    def __init__(self, run_id: str) -> None:
        """Store the active crawl run identifier."""
        super().__init__(run_id)
        self.run_id = run_id

    @override
    def __str__(self) -> str:
        """Describe the active run without exposing database internals."""
        return f"crawl already running: {self.run_id}"


def invalid_storage_input(parameter: str, value: str) -> StorageInputError:
    """Build a typed input error without repeating exception construction sites."""
    return StorageInputError(parameter, value)


def unavailable_storage(reason: str) -> StorageUnavailableError:
    """Build a typed infrastructure error without exposing SQL details."""
    return StorageUnavailableError(reason)


def write_storage_error(
    error: BaseException, operation: str, read_only: bool
) -> ReadOnlyStorageError | StorageUnavailableError:
    """Translate a SQLite write failure into a typed storage failure."""
    message = str(error).lower()
    if read_only or "readonly" in message or "query only" in message:
        return ReadOnlyStorageError(operation)
    return StorageUnavailableError(operation)
