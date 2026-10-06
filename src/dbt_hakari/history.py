"""Turn raw job records into per-node run frequencies and observed billed bytes."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from fnmatch import fnmatchcase
from typing import Literal

from pydantic import Field

from dbt_hakari.backends.base import JobRecord
from dbt_hakari.graph import relation_key
from dbt_hakari.models import FrozenModel, Model
from dbt_hakari.units import Bytes, Count, PositiveInt

UNKNOWN_DAY = "unknown"


class NodeHistory(FrozenModel):
    uid: str
    runs_per_day: float
    billed_p50: Bytes
    n_jobs: PositiveInt
    by_day: dict[str, Bytes] = Field(default_factory=dict)  # day -> largest billed bytes that day
    jobs_by_user: dict[str, Count] = Field(default_factory=dict)

    def recent_max(self, days: set[str]) -> int | None:
        """Largest billed bytes among the given days. The largest, not the median: partial runs
        (``dbt run --empty``, a run on a tiny sample) bill the minimum and say nothing about
        what a full run costs."""
        values = [b for d, b in self.by_day.items() if d in days or d == UNKNOWN_DAY]
        return max(values) if values else None


class ExternalLoad(FrozenModel):
    """Queries not run by dbt (BI tools, analysts) that read one of the project's relations."""

    uid: str
    runs_per_day: float
    bytes_processed: Bytes  # median bytes per run
    jobs_by_user: dict[str, Count] = Field(default_factory=dict)
    bytes_by_user: dict[str, Bytes] = Field(default_factory=dict)  # median per run, per user


class History(Model):
    schema_version: Literal[1] = 1
    lookback_days: PositiveInt
    nodes: dict[str, NodeHistory] = Field(default_factory=dict)
    n_jobs_total: Count = 0
    n_cache_hits: Count = 0
    n_unattributed: Count = 0
    reservation_ids: frozenset[str] = frozenset()
    days: tuple[str, ...] = ()  # every day with at least one job, ascending
    external: dict[str, ExternalLoad] = Field(default_factory=dict)
    external_known: bool = False  # the history was read with the project's relations in mind
    users: dict[str, Count] = Field(default_factory=dict)  # jobs per user, dbt and others

    def recent_days(self, n: int) -> set[str]:
        return set(self.days[-n:])

    def recent_billed(self, uid: str, n_days: int) -> int | None:
        node = self.nodes.get(uid)
        return node.recent_max(self.recent_days(n_days)) if node else None

    def scheduled(self) -> tuple[History, str | None]:
        """Count only what service accounts ran, when people ran dbt too: dbt jobs and queries
        from apps and BI services. A laptop run or an analyst's ad-hoc query is not the schedule.
        Returns the history and a sentence saying what was done, or the history unchanged and
        ``None`` when there is nothing to separate."""
        users: Counter[str] = Counter()
        for node in self.nodes.values():
            users.update(node.jobs_by_user)
        for load in self.external.values():
            users.update(load.jobs_by_user)
        accounts = sorted(u for u in users if is_service_account(u))
        people = [u for u in users if not is_service_account(u)]
        if not accounts or not people:
            return self, None
        total = sum(users.values())
        kept = sum(users[u] for u in accounts)
        note = (
            f"counting only what service accounts ran ({kept} of {total} jobs; "
            f"{len(people)} other user(s) ran the rest, probably laptops and ad-hoc analysis). "
            "Use --all-users to count everyone"
        )
        return self.select_users(only=accounts), note

    def select_users(
        self,
        only: Sequence[str] = (),
        exclude: Sequence[str] = (),
        *,
        include_external: bool = True,
    ) -> History:
        """The history as if only some users had run anything: ``only`` (all when empty) minus
        ``exclude``, both lists of addresses or globs such as ``*@example.com``. Run
        frequencies and the sizes of other people's queries are recomputed; nodes nobody
        selected ran are dropped."""

        def keep(user: str) -> bool:
            return (not only or user_matches(user, only)) and not user_matches(user, exclude)

        days = max(1, self.lookback_days)
        nodes: dict[str, NodeHistory] = {}
        for uid, node in self.nodes.items():
            if not node.jobs_by_user:  # saved before jobs were counted per user: keep as is
                nodes[uid] = node
                continue
            n = sum(c for u, c in node.jobs_by_user.items() if keep(u))
            if n:
                nodes[uid] = node.model_copy(update={"runs_per_day": n / days, "n_jobs": n})
        external: dict[str, ExternalLoad] = {}
        for uid, load in self.external.items():
            if not include_external or not load.jobs_by_user:
                external[uid] = load
                continue
            kept = {u: c for u, c in load.jobs_by_user.items() if keep(u)}
            n = sum(kept.values())
            if n:
                weighted = sum(
                    c * load.bytes_by_user.get(u, load.bytes_processed) for u, c in kept.items()
                )
                external[uid] = load.model_copy(
                    update={"runs_per_day": n / days, "bytes_processed": int(weighted / n)}
                )
        return self.model_copy(update={"nodes": nodes, "external": external})

    @property
    def uses_reservations(self) -> bool:
        return bool(self.reservation_ids)


UNKNOWN_USER = "(unknown)"


def build_history(
    jobs: Iterable[JobRecord],
    lookback_days: int,
    relations: Mapping[str, str] | None = None,
) -> History:
    """Only billed, successful, non-cached SELECT jobs attributable to a dbt node are used.

    Cache hits cost nothing, so they say nothing about billing; script parents duplicate their
    children; both are excluded. With ``relations`` (normalized relation -> uid), queries that
    dbt did not run are attributed to the project's relations they read. Every job is also
    counted per user, so that the history can be restricted to some users later
    (:meth:`History.select_users`).
    """
    external: dict[str, list[tuple[str, int]]] = defaultdict(list)
    billed: dict[str, list[int]] = defaultdict(list)
    dbt_users: dict[str, Counter[str]] = defaultdict(Counter)
    per_day: dict[str, dict[str, int]] = defaultdict(dict)
    seen_days: set[str] = set()
    history = History(lookback_days=lookback_days)
    users: Counter[str] = Counter()
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
        user = job.user or UNKNOWN_USER
        if not job.from_dbt:
            known = relations or {}
            reached = {known[k] for t in job.referenced if (k := relation_key(t)) in known}
            for uid in reached:
                external[uid].append((user, job.processed_bytes))
            if reached:
                users[user] += 1  # once per job, however many relations it read
            continue
        if not job.node_id:
            history.n_unattributed += 1
            continue
        billed[job.node_id].append(job.billed_bytes)
        dbt_users[job.node_id][user] += 1
        users[user] += 1
        day = job.creation_day or UNKNOWN_DAY
        per_day[job.node_id][day] = max(per_day[job.node_id].get(day, 0), job.billed_bytes)
    days = max(1, lookback_days)
    history.external_known = relations is not None
    for uid, runs in external.items():
        by_user: dict[str, list[int]] = defaultdict(list)
        for user, processed in runs:
            by_user[user].append(processed)
        history.external[uid] = ExternalLoad(
            uid=uid,
            runs_per_day=len(runs) / days,
            bytes_processed=int(statistics.median(p for _, p in runs)),
            jobs_by_user={u: len(v) for u, v in by_user.items()},
            bytes_by_user={u: int(statistics.median(v)) for u, v in by_user.items()},
        )
    history.reservation_ids = frozenset(reservations)
    history.days = tuple(sorted(seen_days))
    history.users = dict(users.most_common())
    for uid, values in billed.items():
        history.nodes[uid] = NodeHistory(
            uid=uid,
            runs_per_day=len(values) / days,
            billed_p50=int(statistics.median(values)),
            n_jobs=len(values),
            by_day=dict(per_day[uid]),
            jobs_by_user=dict(dbt_users[uid]),
        )
    return history


def is_service_account(user: str) -> bool:
    return user.lower().endswith(".gserviceaccount.com")


def user_matches(user: str, patterns: Sequence[str]) -> bool:
    return any(fnmatchcase(user.lower(), pattern.lower()) for pattern in patterns)
