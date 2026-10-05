"""The MILP must agree with exhaustive search on random DAGs (the 'competitive programming'
check): same optimum for every K, and the greedy baseline can never beat it."""

from __future__ import annotations

from itertools import combinations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dbt_hakari.cost import BigQueryOnDemand
from dbt_hakari.graph import Graph, Node, NodeKind
from dbt_hakari.optimize.builder import build_model
from dbt_hakari.optimize.evaluate import evaluate
from dbt_hakari.optimize.greedy import greedy
from dbt_hakari.optimize.queries import Query
from dbt_hakari.optimize.solvers.base import SolveStatus
from dbt_hakari.optimize.solvers.scipy_highs import ScipyHighs

MIB = 1024**2
COST = BigQueryOnDemand()
SOLVER = ScipyHighs()


@st.composite
def worlds(draw):
    n_sources = draw(st.integers(1, 5))
    nodes: list[Node] = []
    pool: list[str] = []
    for i in range(n_sources):
        nodes.append(Node(f"s{i}", f"s{i}", NodeKind.SOURCE))
        pool.append(f"s{i}")
    views: list[str] = []
    for i in range(draw(st.integers(1, 7))):
        kind = draw(st.sampled_from([NodeKind.VIEW_MODEL] * 4 + [NodeKind.EPHEMERAL_MODEL]))
        parents = draw(st.lists(st.sampled_from(pool), min_size=1, max_size=3, unique=True))
        uid = f"v{i}"
        nodes.append(Node(uid, uid, kind, parents=tuple(parents)))
        pool.append(uid)
        views.append(uid) if kind is NodeKind.VIEW_MODEL else None
    executing: list[str] = []
    for i in range(draw(st.integers(0, 2))):
        parents = draw(st.lists(st.sampled_from(pool), min_size=1, max_size=3, unique=True))
        uid = f"m{i}"
        nodes.append(Node(uid, uid, NodeKind.TABLE_MODEL, parents=tuple(parents)))
        pool.append(uid)
        executing.append(uid)
    for i in range(draw(st.integers(1, 5))):
        parents = draw(st.lists(st.sampled_from(pool), min_size=1, max_size=3, unique=True))
        uid = f"t{i}"
        nodes.append(Node(uid, uid, NodeKind.TEST, parents=tuple(parents)))
        executing.append(uid)
    graph = Graph({n.uid: n for n in nodes})

    leaf_weight = {f"s{i}": draw(st.integers(1, 3)) for i in range(n_sources)}
    mib = st.integers(0, 60).map(lambda m: m * MIB)
    queries = [
        Query(uid, graph.nodes[uid].parents, draw(mib), draw(st.integers(1, 3)))
        for uid in executing
    ]
    candidates = [v for v in views if draw(st.booleans())]
    for v in candidates:
        queries.append(
            Query(
                f"build:{v}", graph.nodes[v].parents, draw(mib), draw(st.integers(1, 2)), build_of=v
            )
        )
    return graph, queries, candidates, leaf_weight


def brute_force(graph, queries, candidates, leaf_weight, k):
    best = float("inf")
    for size in range(k + 1):
        for subset in combinations(candidates, size):
            best = min(best, evaluate(subset, queries, graph, leaf_weight, COST))
    return best


def solve(graph, queries, candidates, leaf_weight, k):
    model = build_model(graph, queries, candidates, leaf_weight, COST, max_views=k)
    result = SOLVER.solve(model.ir, 30.0, 1e-9)
    assert result.status is SolveStatus.OPTIMAL
    return model.selection(result.values)


@settings(max_examples=250, deadline=None)
@given(worlds(), st.integers(0, 4))
def test_milp_optimum_equals_exhaustive_search(world, k):
    graph, queries, candidates, leaf_weight = world
    k = min(k, len(candidates))
    chosen = solve(graph, queries, candidates, leaf_weight, k)
    assert len(chosen) <= k
    achieved = evaluate(chosen, queries, graph, leaf_weight, COST)
    optimum = brute_force(graph, queries, candidates, leaf_weight, k)
    assert achieved == pytest.approx(optimum, rel=1e-6, abs=1.0)


@settings(max_examples=120, deadline=None)
@given(worlds())
def test_cost_never_increases_with_k_and_greedy_never_wins(world):
    graph, queries, candidates, leaf_weight = world
    cost = lambda s: evaluate(s, queries, graph, leaf_weight, COST)  # noqa: E731
    path = greedy(candidates, cost, len(candidates))
    previous = float("inf")
    for k in range(len(candidates) + 1):
        optimum = cost(solve(graph, queries, candidates, leaf_weight, k))
        assert optimum <= previous * (1 + 1e-9) + 1.0
        assert optimum <= cost(path[k]) * (1 + 1e-9) + 1.0
        previous = optimum
