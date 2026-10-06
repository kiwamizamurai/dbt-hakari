"""What is optimized, stated once.

A *candidate* is a model that can be either a view or a table. ``tables`` is the set of
candidates that are tables; the baseline is the set that are tables today. Everything that is
not a candidate is fixed: sources, seeds, snapshots, incremental models and the like are tables,
ephemeral models and ineligible views are always expanded.

A *query* reads some nodes. Views (and candidates that are not tables) are expanded to what is
beneath them; the tables that remain are what BigQuery bills. With ``kappa`` set, a query reads
the same fraction ``kappa`` of every table it touches, so the bytes it reads from a table of
size ``F`` are ``kappa * F``. ``kappa`` is fitted at the baseline so that the bytes add up to the
dry-run total of the query. Without ``kappa`` the total is a constant.

``evaluate`` below is the definition of the cost. The MILP in ``builder`` must agree with it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from dbt_hakari.cost.base import CostModel
from dbt_hakari.graph import EXPANDABLE_KINDS, Graph


@dataclass(frozen=True)
class Query:
    key: str
    parents: tuple[str, ...]
    weight: float  # runs per day
    bytes_processed: float  # dry-run total, at the baseline
    kappa: float | None = None  # fraction of every table it reads; None: bytes are constant
    build_of: str | None = None  # the build of a candidate: only runs when it is a table


@dataclass(frozen=True)
class Candidate:
    uid: str
    baseline_table: bool
    size: float  # bytes of its table (known, or estimated when it is a view today)
    # What reading it as a *view* is assumed to cost, in bytes, for a table today: what its SQL
    # scans (the dry-run of its build), as if every read recomputed all of it. A view usually
    # costs less (BigQuery only reads the columns a reader needs: 33 MiB instead of 93 MiB in a
    # measured case), so this errs on the side of keeping tables as tables. Without it, a table
    # that took 3 GiB to build and is 200 MiB large would look nearly free to turn into a view
    # for a reader that needs one column. 0 for a view today: its readers' bytes are measured.
    floor: float = 0.0


@dataclass
class Problem:
    graph: Graph
    candidates: dict[str, Candidate]
    sizes: Mapping[str, float]  # bytes of the fixed tables
    leaf_weight: Mapping[str, int]
    queries: list[Query]
    cost_model: CostModel

    @property
    def baseline(self) -> frozenset[str]:
        return frozenset(u for u, c in self.candidates.items() if c.baseline_table)

    def flips(self, tables: frozenset[str]) -> frozenset[str]:
        """The candidates whose materialization differs from today's."""
        return tables ^ self.baseline

    def tables_from_flips(self, flips: Iterable[str]) -> frozenset[str]:
        return self.baseline ^ frozenset(flips)

    def size_of(self, uid: str) -> float:
        candidate = self.candidates.get(uid)
        return candidate.size if candidate is not None else self.sizes[uid]

    def is_expanded(self, uid: str, tables: frozenset[str]) -> bool:
        if uid in self.candidates:
            return uid not in tables
        return self.graph.nodes[uid].kind in EXPANDABLE_KINDS

    def reaches(self, parents: Iterable[str], uid: str) -> bool:
        """Whether a query reading ``parents`` can touch ``uid``, in some setup of the candidates:
        ``uid`` is read directly, or beneath a view or a candidate that may be expanded."""
        seen: set[str] = set()
        stack = list(parents)
        while stack:
            current = stack.pop()
            if current == uid:
                return True
            if current in seen:
                continue
            seen.add(current)
            if current in self.candidates or self.graph.nodes[current].kind in EXPANDABLE_KINDS:
                stack.extend(self.graph.nodes[current].parents)
        return False

    def walk(self, parents: Iterable[str], tables: frozenset[str]) -> tuple[dict[str, int], float]:
        """What a query reading ``parents`` touches: the physical tables, each with the number of
        tables it stands for (more than one only for a wildcard), and the bytes it must at least
        read because it computes views that are tables today (``Candidate.floor``)."""
        seen: set[str] = set()
        stack = list(parents)
        found: dict[str, int] = {}
        floor = 0.0
        while stack:
            uid = stack.pop()
            if uid in seen:
                continue
            seen.add(uid)
            if self.is_expanded(uid, tables):
                if uid in self.candidates:
                    floor += self.candidates[uid].floor
                stack.extend(self.graph.nodes[uid].parents)
            else:
                found[uid] = self.leaf_weight.get(uid, 1)
        return found, floor

    def tables_read(self, parents: Iterable[str], tables: frozenset[str]) -> dict[str, int]:
        return self.walk(parents, tables)[0]

    def fit_kappa(self, parents: Iterable[str], bytes_processed: float) -> float | None:
        """The fraction of its tables a query reads, from today's dry-run total."""
        read = self.tables_read(parents, self.baseline)
        known = all(uid in self.candidates or uid in self.sizes for uid in read)
        total = sum(self.size_of(uid) for uid in read) if known else 0.0
        if not read or not known or total <= 0:
            return None
        return bytes_processed / total


def bill(problem: Problem, query: Query, reads: Mapping[str, int], floor: float = 0.0) -> float:
    cost = problem.cost_model
    if query.kappa is None:
        return cost.billed_bytes(max(query.bytes_processed, floor), sum(reads.values()))
    parts = [(query.kappa * problem.size_of(uid), omega) for uid, omega in reads.items()]
    return cost.billed_bytes(max(sum(b for b, _ in parts), floor), sum(omega for _, omega in parts))


def evaluate(problem: Problem, tables: Iterable[str]) -> float:
    """Billed bytes per day when exactly ``tables`` of the candidates are tables."""
    chosen = frozenset(tables)
    total = 0.0
    for query in problem.queries:
        if query.build_of is not None and query.build_of not in chosen:
            continue  # a view is never built
        reads, floor = problem.walk(query.parents, chosen)
        total += query.weight * bill(problem, query, reads, floor)
    return total
