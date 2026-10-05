"""Cost of a given selection, computed straight from the definition.

Deliberately independent of the MILP: it expands the graph and counts tables, nothing else. It is
the oracle for the tests and the objective used to compare against the greedy baseline.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from dbt_hakari.cost.base import CostModel
from dbt_hakari.graph import Graph
from dbt_hakari.optimize.queries import Query


def evaluate(
    selection: Iterable[str],
    queries: Iterable[Query],
    graph: Graph,
    leaf_weight: Mapping[str, int],
    cost_model: CostModel,
) -> float:
    """Billed bytes per day if the views in ``selection`` are tables."""
    chosen = frozenset(selection)
    total = 0.0
    for query in queries:
        if query.build_of is not None and query.build_of not in chosen:
            continue  # a view that stays a view is never built
        tables = graph.table_count_from(query.parents, leaf_weight, chosen)
        total += query.weight * cost_model.billed_bytes(query.bytes_processed, tables)
    return total
