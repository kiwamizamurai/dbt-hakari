"""The queries that run every day, assembled into a :class:`Problem`.

Four kinds: models that stay as they are (incremental, or tables we may not change), tests
(grouped when identical), queries other people run against the project's relations, and the
build of every candidate (it only runs when the candidate is a table).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping

from dbt_hakari.cost.base import CostModel
from dbt_hakari.costdata import CostData
from dbt_hakari.graph import Graph
from dbt_hakari.history import History
from dbt_hakari.optimize.problem import Candidate, Problem, Query


def runs_per_day(uid: str, history: History | None, assume_daily: bool) -> float:
    if assume_daily or history is None:
        return 1.0
    node = history.nodes.get(uid)
    return node.runs_per_day if node else 1.0


def build_frequency(graph: Graph, history: History | None, assume_daily: bool) -> float:
    """How often a view that becomes a table would be built: as often as the project's own
    table models, because one ``dbt run`` builds them all. A view has no job of its own, so its
    frequency cannot be read from the history directly."""
    if assume_daily or history is None:
        return 1.0
    rates = [
        history.nodes[uid].runs_per_day for uid in graph.executing_models() if uid in history.nodes
    ]
    return statistics.median(rates) if rates else 1.0


def build_problem(
    graph: Graph,
    cost_data: CostData,
    history: History | None,
    cost_model: CostModel,
    candidates: Mapping[str, Candidate],
    *,
    skip: frozenset[str] = frozenset(),
    assume_daily: bool = False,
    build_runs_per_day: float | None = None,
) -> Problem:
    sizes = {uid: float(b) for uid, b in cost_data.leaf_bytes.items() if uid not in candidates}
    problem = Problem(
        graph=graph,
        candidates=dict(candidates),
        sizes=sizes,
        leaf_weight=cost_data.leaf_weight,
        queries=[],
        cost_model=cost_model,
    )
    if build_runs_per_day is None:
        build_runs_per_day = build_frequency(graph, history, assume_daily)

    def query(
        key: str,
        parents: tuple[str, ...],
        weight: float,
        bytes_processed: float,
        build_of: str | None = None,
    ) -> Query:
        kappa = problem.fit_kappa(parents, bytes_processed)
        return Query(key, parents, weight, bytes_processed, kappa, build_of)

    queries = problem.queries
    for uid in graph.executing_models():
        cost = cost_data.nodes.get(uid)
        if uid in candidates or cost is None or cost.error or uid in skip:
            continue
        queries.append(
            query(
                uid,
                graph.nodes[uid].parents,
                runs_per_day(uid, history, assume_daily),
                cost.bytes_processed,
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
        queries.append(query(f"tests:{uids[0]}+{len(uids) - 1}", parents, weight, processed))

    if history is not None:
        for uid, load in sorted(history.external.items()):
            if uid in graph.nodes:
                queries.append(
                    query(f"external:{uid}", (uid,), load.runs_per_day, load.bytes_processed)
                )

    for uid in sorted(candidates):
        cost = cost_data.nodes[uid]
        own = runs_per_day(uid, history, assume_daily) if candidates[uid].baseline_table else None
        queries.append(
            query(
                f"build:{uid}",
                graph.nodes[uid].parents,
                build_runs_per_day if own is None else own,
                cost.bytes_processed,
                build_of=uid,
            )
        )
    return problem
