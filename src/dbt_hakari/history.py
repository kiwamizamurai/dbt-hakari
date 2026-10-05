"""Turn raw job records into per-node run frequencies and observed billed bytes."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable

from pydantic import Field

from dbt_hakari.backends.base import JobRecord
from dbt_hakari.models import FrozenModel, Model
from dbt_hakari.units import Bytes, Count, PositiveInt

UNKNOWN_DAY = "unknown"


class NodeHistory(FrozenModel):
    uid: str
    runs_per_day: float
    billed_p50: Bytes
    n_jobs: PositiveInt
    by_day: dict[str, Bytes] = Field(default_factory=dict)  # day -> largest billed bytes that day

    def recent_max(self, days: set[str]) -> int | None:
        """Largest billed bytes among the given days. The largest, not the median: partial runs
        (``dbt run --empty``, a run on a tiny sample) bill the minimum and say nothing about
        what a full run costs."""
        values = [b for d, b in self.by_day.items() if d in days or d == UNKNOWN_DAY]
        return max(values) if values else None


class History(Model):
    lookback_days: PositiveInt
    nodes: dict[str, NodeHistory] = Field(default_factory=dict)
    n_jobs_total: Count = 0
    n_cache_hits: Count = 0
    n_unattributed: Count = 0
    reservation_ids: frozenset[str] = frozenset()
    days: tuple[str, ...] = ()  # every day with at least one job, ascending

    def recent_days(self, n: int) -> set[str]:
        return set(self.days[-n:])

    def recent_billed(self, uid: str, n_days: int) -> int | None:
        node = self.nodes.get(uid)
        return node.recent_max(self.recent_days(n_days)) if node else None

    @property
    def uses_reservations(self) -> bool:
        return bool(self.reservation_ids)


def build_history(jobs: Iterable[JobRecord], lookback_days: int) -> History:
    """Only billed, successful, non-cached SELECT jobs attributable to a dbt node are used.

    Cache hits cost nothing, so they say nothing about billing; script parents duplicate their
    children; both are excluded.
    """
    billed: dict[str, list[int]] = defaultdict(list)
    per_day: dict[str, dict[str, int]] = defaultdict(dict)
    seen_days: set[str] = set()
    history = History(lookback_days=lookback_days)
    reservations: set[str] = set()
    for job in jobs:
        history.n_jobs_total += 1
        if job.creation_day:
            seen_days.add(job.creation_day)
        if job.reservation_id:
            reservations.add(job.reservation_id)
        if job.cache_hit:
            history.n_cache_hits += 1
            continue
        if job.error or job.billed_bytes <= 0 or job.statement_type == "SCRIPT":
            continue
        if not job.node_id:
            history.n_unattributed += 1
            continue
        billed[job.node_id].append(job.billed_bytes)
        day = job.creation_day or UNKNOWN_DAY
        per_day[job.node_id][day] = max(per_day[job.node_id].get(day, 0), job.billed_bytes)
    history.reservation_ids = frozenset(reservations)
    history.days = tuple(sorted(seen_days))
    days = max(1, lookback_days)
    for uid, values in billed.items():
        history.nodes[uid] = NodeHistory(
            uid=uid,
            runs_per_day=len(values) / days,
            billed_p50=int(statistics.median(values)),
            n_jobs=len(values),
            by_day=dict(per_day[uid]),
        )
    return history
