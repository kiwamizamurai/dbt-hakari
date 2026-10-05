from __future__ import annotations


class KannaError(Exception):
    """Base class for all errors raised by dbt-hakari."""

    exit_code = 1


class UsageError(KannaError):
    exit_code = 2


class ManifestError(KannaError):
    exit_code = 3


class GateFailedError(KannaError):
    """The trust gate (verify) failed, so optimization is refused."""

    exit_code = 4


class BackendError(KannaError):
    exit_code = 5


class SolverTimeLimit(KannaError):
    """The solver stopped on its time limit before reaching the requested gap."""

    exit_code = 6
