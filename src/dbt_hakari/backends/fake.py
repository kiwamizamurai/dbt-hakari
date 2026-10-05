from __future__ import annotations

from collections.abc import Callable, Iterable

from dbt_hakari.backends.base import DryRunResult, JobRecord
from dbt_hakari.errors import BackendError


class FakeBackend:
    """Deterministic in-memory backend for tests (no credentials needed)."""

    def __init__(
        self,
        dry_runs: dict[str, DryRunResult] | Callable[[str], DryRunResult] | None = None,
        jobs: Iterable[JobRecord] = (),
    ) -> None:
        self._dry_runs = dry_runs or {}
        self._jobs = list(jobs)
        self.dry_run_calls: list[str] = []

    def dry_run(self, sql: str) -> DryRunResult:
        self.dry_run_calls.append(sql)
        if callable(self._dry_runs):
            return self._dry_runs(sql)
        try:
            return self._dry_runs[sql]
        except KeyError as error:
            raise BackendError(f"FakeBackend has no dry-run result for: {sql[:60]!r}") from error

    def list_jobs(self, lookback_days: int) -> Iterable[JobRecord]:
        return list(self._jobs)
