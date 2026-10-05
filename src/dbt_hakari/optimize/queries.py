"""The set of queries that run every day, and what each one reads.

Three kinds: table/incremental model builds, tests (grouped when identical), and the build of a
view *if* it were materialized.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from dbt_hakari.costdata import CostData
from dbt_hakari.graph import Graph
from dbt_hakari.history import History


@dataclass(frozen=True)
class Query:
    key: str
    parents: tuple[str, ...]
    bytes_processed: int
    weight: float  # runs per day
    build_of: str | None = None  # set for "the build of view X if X is materialized"


def runs_per_day(uid: str, history: History | None, assume_daily: bool) -> float:
    if assume_daily or history is None:
        return 1.0
    node = history.nodes.get(uid)
    return node.runs_per_day if node else 1.0


def build_queries(
    graph: Graph,
    cost_data: CostData,
    history: History | None,
    candidates: list[str],
    *,
    skip: frozenset[str] = frozenset(),
    assume_daily: bool = False,
    build_runs_per_day: float = 1.0,
) -> list[Query]:
    queries: list[Query] = []
    for uid in graph.executing_models():
        cost = cost_data.nodes.get(uid)
        if cost is None or cost.error or uid in skip:
            continue
        queries.append(
            Query(
                uid,
                graph.nodes[uid].parents,
                cost.bytes_processed,
                runs_per_day(uid, history, assume_daily),
            )
        )

    grouped: dict[tuple[tuple[str, ...], int], list[str]] = defaultdict(list)
    for uid in graph.tests():
        cost = cost_data.nodes.get(uid)
        if cost is None or cost.error or uid in skip:
            continue
        parents = tuple(sorted(graph.nodes[uid].parents))
        grouped[(parents, cost.bytes_processed)].append(uid)
    for (parents, processed), uids in sorted(grouped.items()):
        weight = sum(runs_per_day(u, history, assume_daily) for u in uids)
        queries.append(Query(f"tests:{uids[0]}+{len(uids) - 1}", parents, processed, weight))

    for uid in candidates:
        cost = cost_data.nodes[uid]
        queries.append(
            Query(
                f"build:{uid}",
                graph.nodes[uid].parents,
                cost.bytes_processed,
                build_runs_per_day,
                build_of=uid,
            )
        )
    return queries
