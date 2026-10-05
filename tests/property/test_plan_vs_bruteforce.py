"""End to end: optimize() on random dbt projects must agree with exhaustive search.

The oracle is written from the definition and shares nothing with the code under test except
``evaluate`` (itself checked against hand-computed examples): every executing model and every
test is its own query, and a view that becomes a table adds one build query.
"""

from __future__ import annotations

import os
from itertools import combinations, pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dbt_hakari.cost import BigQueryOnDemand
from dbt_hakari.costdata import CostData, NodeCost
from dbt_hakari.graph import Graph, Node, NodeKind
from dbt_hakari.optimize import OptimizeOptions, optimize
from dbt_hakari.optimize.evaluate import evaluate
from dbt_hakari.optimize.queries import Query
from dbt_hakari.optimize.solvers.scipy_highs import ScipyHighs

MIB = 1024**2
COST = BigQueryOnDemand()
SOLVER = ScipyHighs()
EXAMPLES = int(os.environ.get("HAKARI_FUZZ", "120"))


@st.composite
def projects(draw):
    nodes: list[Node] = []
    pool: list[str] = []
    n_sources = draw(st.integers(1, 5))
    for i in range(n_sources):
        nodes.append(Node(f"s{i}", f"s{i}", NodeKind.SOURCE, relation=f"p.d.s{i}"))
        pool.append(f"s{i}")
    for i in range(draw(st.integers(1, 6))):
        kind = draw(st.sampled_from([NodeKind.VIEW_MODEL] * 4 + [NodeKind.EPHEMERAL_MODEL]))
        parents = draw(st.lists(st.sampled_from(pool), min_size=1, max_size=3, unique=True))
        nodes.append(Node(f"v{i}", f"v{i}", kind, parents=tuple(parents)))
        pool.append(f"v{i}")
    for i in range(draw(st.integers(0, 3))):
        kind = draw(st.sampled_from([NodeKind.TABLE_MODEL, NodeKind.INCREMENTAL_MODEL]))
        parents = draw(st.lists(st.sampled_from(pool), min_size=1, max_size=3, unique=True))
        nodes.append(Node(f"m{i}", f"m{i}", kind, parents=tuple(parents)))
        pool.append(f"m{i}")
    for i in range(draw(st.integers(1, 6))):
        parents = draw(st.lists(st.sampled_from(pool), min_size=1, max_size=3, unique=True))
        nodes.append(Node(f"t{i}", f"t{i}", NodeKind.TEST, parents=tuple(parents)))
    graph = Graph({n.uid: n for n in nodes})

    leaf_weight = {f"s{i}": draw(st.integers(1, 3)) for i in range(n_sources)}
    sizes = st.integers(0, 80).map(lambda m: m * MIB)
    costs = {}
    for uid, node in graph.nodes.items():
        if node.kind in (NodeKind.SOURCE, NodeKind.EPHEMERAL_MODEL):
            continue
        costs[uid] = NodeCost(
            uid=uid,
            bytes_processed=draw(sizes),
            n_tables=graph.table_count(uid, leaf_weight),
        )
    data = CostData(nodes=costs, leaf_weight={u: w for u, w in leaf_weight.items() if w != 1})
    return graph, data, leaf_weight


def oracle_queries(graph: Graph, data: CostData) -> tuple[list[Query], list[str]]:
    views = [uid for uid in graph.views() if uid in data.nodes]
    queries = [
        Query(uid, graph.nodes[uid].parents, data.nodes[uid].bytes_processed, 1.0)
        for uid in [*graph.executing_models(), *graph.tests()]
        if uid in data.nodes
    ]
    queries += [
        Query(f"build:{v}", graph.nodes[v].parents, data.nodes[v].bytes_processed, 1.0, build_of=v)
        for v in views
    ]
    return queries, views


def best_for(graph, queries, views, leaf_weight, k):
    best = float("inf")
    for size in range(min(k, len(views)) + 1):
        for subset in combinations(views, size):
            best = min(best, evaluate(subset, queries, graph, leaf_weight, COST))
    return best


@settings(max_examples=EXAMPLES, deadline=None)
@given(projects())
def test_the_plan_matches_exhaustive_search_for_every_k(project):
    graph, data, leaf_weight = project
    queries, views = oracle_queries(graph, data)
    plan = optimize(
        graph,
        data,
        None,
        COST,
        OptimizeOptions(max_k=len(views), assume_daily=True),
        solver=SOLVER,
    )

    baseline = evaluate((), queries, graph, leaf_weight, COST)
    assert plan.baseline_bytes_per_day == pytest.approx(baseline, rel=1e-9, abs=1.0)
    assert plan.n_candidates == len(views)

    for point in plan.curve:
        optimum = best_for(graph, queries, views, leaf_weight, point.k)
        assert point.objective_bytes_per_day == pytest.approx(optimum, rel=1e-6, abs=1.0)
        # what the plan reports is what the chosen set really costs
        achieved = evaluate(point.chosen, queries, graph, leaf_weight, COST)
        assert achieved == pytest.approx(point.objective_bytes_per_day, rel=1e-6, abs=1.0)
        assert len(point.chosen) <= point.k

    if plan.unconstrained is not None:
        overall = best_for(graph, queries, views, leaf_weight, len(views))
        assert plan.unconstrained.objective_bytes_per_day == pytest.approx(
            overall, rel=1e-6, abs=1.0
        )

    costs = [p.objective_bytes_per_day for p in plan.curve]
    assert all(b <= a * (1 + 1e-9) + 1.0 for a, b in pairwise(costs))


@settings(max_examples=max(30, EXAMPLES // 3), deadline=None)
@given(projects(), st.data())
def test_forced_choices_are_respected_and_still_optimal(project, data_strategy):
    graph, data, leaf_weight = project
    queries, views = oracle_queries(graph, data)
    if len(views) < 2:
        return
    from dbt_hakari.optimize.builder import build_model

    forced_table = data_strategy.draw(st.sampled_from(views))
    forced_view = data_strategy.draw(st.sampled_from([v for v in views if v != forced_table]))
    model = build_model(
        graph,
        queries,
        views,
        leaf_weight,
        COST,
        max_views=None,
        force_table=[forced_table],
        force_view=[forced_view],
    )
    result = SOLVER.solve(model.ir, 30.0, 1e-9)
    chosen = model.selection(result.values)
    assert forced_table in chosen and forced_view not in chosen

    others = [v for v in views if v not in (forced_table, forced_view)]
    expected = min(
        evaluate((forced_table, *subset), queries, graph, leaf_weight, COST)
        for size in range(len(others) + 1)
        for subset in combinations(others, size)
    )
    assert evaluate(chosen, queries, graph, leaf_weight, COST) == pytest.approx(
        expected, rel=1e-6, abs=1.0
    )


def naive_cost(graph, query, chosen, leaf_weight):
    """The bill of one query, written as plainly as possible: walk the SQL's references, expand
    views that are not tables, count what is left."""
    tables, seen, stack = set(), set(), list(query.parents)
    while stack:
        uid = stack.pop()
        if uid in seen:
            continue
        seen.add(uid)
        node = graph.nodes[uid]
        if node.kind is NodeKind.EPHEMERAL_MODEL or (
            node.kind is NodeKind.VIEW_MODEL and uid not in chosen
        ):
            stack.extend(node.parents)
        else:
            tables.add(uid)
    n_tables = sum(leaf_weight.get(uid, 1) for uid in tables)
    return max(query.bytes_processed, 10 * MIB * n_tables, 10 * MIB)


@settings(max_examples=max(60, EXAMPLES), deadline=None)
@given(projects(), st.data())
def test_evaluate_agrees_with_a_naive_reimplementation(project, data_strategy):
    graph, data, leaf_weight = project
    queries, views = oracle_queries(graph, data)
    chosen = frozenset(v for v in views if data_strategy.draw(st.booleans()))
    expected = sum(
        naive_cost(graph, q, chosen, leaf_weight)
        for q in queries
        if q.build_of is None or q.build_of in chosen
    )
    assert evaluate(chosen, queries, graph, leaf_weight, COST) == pytest.approx(
        expected, rel=1e-9, abs=1.0
    )


@settings(max_examples=max(60, EXAMPLES), deadline=None)
@given(projects(), st.data())
def test_run_frequencies_from_history_reach_the_objective(project, data_strategy):
    """Readers weigh in at their own run frequency; a view that becomes a table is built as often
    as the project's table models (median)."""
    import statistics

    from dbt_hakari.history import History, NodeHistory

    graph, data, leaf_weight = project
    rates = {}
    for uid in [*graph.executing_models(), *graph.tests()]:
        if uid in data.nodes and data_strategy.draw(st.booleans()):
            rates[uid] = data_strategy.draw(st.sampled_from([0.5, 1.0, 2.0, 4.0]))
    history = History(
        lookback_days=14,
        nodes={
            uid: NodeHistory(uid=uid, runs_per_day=rate, billed_p50=MIB, n_jobs=1)
            for uid, rate in rates.items()
        },
    )

    model_rates = [rates[u] for u in graph.executing_models() if u in rates]
    build_rate = statistics.median(model_rates) if model_rates else 1.0
    views = [uid for uid in graph.views() if uid in data.nodes]
    queries = [
        Query(
            uid,
            graph.nodes[uid].parents,
            data.nodes[uid].bytes_processed,
            rates.get(uid, 1.0),
        )
        for uid in [*graph.executing_models(), *graph.tests()]
        if uid in data.nodes
    ]
    queries += [
        Query(
            f"build:{v}",
            graph.nodes[v].parents,
            data.nodes[v].bytes_processed,
            build_rate,
            build_of=v,
        )
        for v in views
    ]

    plan = optimize(
        graph,
        data,
        history,
        COST,
        OptimizeOptions(max_k=len(views)),
        solver=SOLVER,
    )
    for point in plan.curve:
        optimum = best_for(graph, queries, views, leaf_weight, point.k)
        assert point.objective_bytes_per_day == pytest.approx(optimum, rel=1e-6, abs=1.0)
