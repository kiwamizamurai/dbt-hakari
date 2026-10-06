from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DryRunResult:
    bytes_processed: int
    referenced_tables: frozenset[str]


@dataclass(frozen=True)
class JobRecord:
    """One finished BigQuery query job, as seen in INFORMATION_SCHEMA.JOBS."""

    node_id: str | None
    billed_bytes: int
    cache_hit: bool = False
    error: bool = False
    statement_type: str = "SELECT"
    reservation_id: str | None = None
    creation_day: str | None = None  # YYYY-MM-DD
    processed_bytes: int = 0
    from_dbt: bool = True  # False: a query somebody else ran (a BI tool, an analyst)
    referenced: tuple[str, ...] = ()  # project.dataset.table, only kept for non-dbt jobs
    user: str | None = None  # who ran it (a person's address or a service account)


class Backend(Protocol):
    """All I/O against the warehouse. Implementations must be strictly read-only."""

    def dry_run(self, sql: str) -> DryRunResult: ...

    def list_jobs(self, lookback_days: int) -> Iterable[JobRecord]: ...
