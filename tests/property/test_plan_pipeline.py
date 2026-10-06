"""optimize() end to end on random projects: dry-run totals in, a plan out."""

from __future__ import annotations

import os
from itertools import combinations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dbt_hakari.cost import BigQueryOnDemand
from dbt_hakari.costdata import CostData, NodeCost
from dbt_hakari.graph import Graph, Node, NodeKind
from dbt_hakari.history import ExternalLoad, History, NodeHistory
from dbt_hakari.optimize import Direction, OptimizeOptions, optimize
from dbt_hakari.optimize.eligibility import select_candidates
from dbt_hakari.optimize.problem import evaluate
from dbt_hakari.optimize.queries import build_problem
from dbt_hakari.optimize.solvers.scipy_highs import ScipyHighs

MIB = 1024**2
COST = BigQueryOnDemand()
SOLVER = ScipyHighs()
EXAMPLES = int(os.environ.get("HAKARI_FUZZ", "120"))


@st.composite
def projects(draw):
    nodes: list[Node] = []
    pool: list[str] = []
    n_sources = draw(st.integers(1, 4))
    for i in range(n_sources):
        nodes.append(Node(f"s{i}", f"s{i}", NodeKind.SOURCE))
        pool.append(f"s{i}")

    def parents_of():
        return tuple(draw(st.lists(st.sampled_from(pool), min_size=1, max_size=3, unique=True)))

    kinds = [NodeKind.VIEW_MODEL] * 3 + [NodeKind.TABLE_MODEL] * 2 + [NodeKind.EPHEMERAL_MODEL]
    for i in range(draw(st.integers(1, 6))):
        kind = draw(st.sampled_from(kinds))
        nodes.append(Node(f"m{i}", f"m{i}", kind, parents=parents_of()))
        pool.append(f"m{i}")
    for i in range(draw(st.integers(0, 2))):
        nodes.append(Node(f"i{i}", f"i{i}", NodeKind.INCREMENTAL_MODEL, parents=parents_of()))
        pool.append(f"i{i}")
    tests = []
    for i in range(draw(st.integers(1, 5))):
        nodes.append(Node(f"t{i}", f"t{i}", NodeKind.TEST, parents=parents_of()))
        tests.append(f"t{i}")
    exposed = (
        frozenset(n.uid for n in nodes if n.kind is NodeKind.TABLE_MODEL and draw(st.booleans()))
        if draw(st.booleans())
        else frozenset()
    )
    graph = Graph({n.uid: n for n in nodes}, exposed)

    leaf_weight = {f"s{i}": draw(st.integers(1, 3)) for i in range(n_sources)}
    size = st.integers(1, 80).map(lambda m: m * MIB)
    costs, leaf_bytes = {}, {}
    for uid, node in graph.nodes.items():
        if node.kind in (NodeKind.SOURCE, NodeKind.INCREMENTAL_MODEL, NodeKind.TABLE_MODEL):
            leaf_bytes[uid] = draw(size)
        if node.kind in (NodeKind.SOURCE, NodeKind.EPHEMERAL_MODEL):
            continue
        costs[uid] = NodeCost(
            uid=uid,
            bytes_processed=draw(st.integers(0, 60)) * MIB,
            n_tables=graph.table_count(uid, leaf_weight),
        )
    data = CostData(
        nodes=costs,
        leaf_weight={u: w for u, w in leaf_weight.items() if w != 1},
        leaf_bytes=leaf_bytes,
    )

    rates = {
        uid: draw(st.sampled_from([0.5, 1.0, 2.0]))
        for uid in [*graph.executing_models(), *tests]
        if draw(st.booleans())
    }
    history = History(
        lookback_days=14,
        nodes={
            uid: NodeHistory(uid=uid, runs_per_day=r, billed_p50=MIB, n_jobs=1)
            for uid, r in rates.items()
        },
        external_known=True,
        external={
            uid: ExternalLoad(
                uid=uid,
                runs_per_day=draw(st.sampled_from([0.5, 3.0])),
                bytes_processed=draw(st.integers(0, 60)) * MIB,
            )
            for uid, node in graph.nodes.items()
            if node.kind in (NodeKind.VIEW_MODEL, NodeKind.TABLE_MODEL) and draw(st.booleans())
        },
    )
    return graph, data, history


def brute_force(problem, k):
    names = sorted(problem.candidates)
    best = float("inf")
    for size in range(len(names) + 1):
        for subset in combinations(names, size):
            tables = frozenset(subset)
            if len(problem.flips(tables)) <= k:
                best = min(best, evaluate(problem, tables))
    return best


def naive_baseline(problem) -> float:
    """Today's bill from the dry-run totals only: weight x max(bytes, 10 MiB x tables, 10 MiB)."""
    graph, today = problem.graph, problem.baseline
    total = 0.0
    for q in problem.queries:
        if q.build_of is not None and q.build_of not in today:
            continue
        tables, seen, stack = {}, set(), list(q.parents)
        while stack:
            uid = stack.pop()
            if uid in seen:
                continue
            seen.add(uid)
            node = graph.nodes[uid]
            expanded = (uid in problem.candidates and uid not in today) or (
                uid not in problem.candidates
                and node.kind in (NodeKind.VIEW_MODEL, NodeKind.EPHEMERAL_MODEL)
            )
            if expanded:
                stack.extend(node.parents)
            else:
                tables[uid] = problem.leaf_weight.get(uid, 1)
        total += q.weight * max(q.bytes_processed, 10 * MIB * sum(tables.values()), 10 * MIB)
    return total


@settings(max_examples=EXAMPLES, deadline=None)
@given(projects(), st.sampled_from(list(Direction)))
def test_the_plan_matches_exhaustive_search(project, direction):
    graph, data, history = project
    options = OptimizeOptions(max_k=6, assume_daily=False, direction=direction)
    plan = optimize(graph, data, history, COST, options, solver=SOLVER, compare_greedy=True)

    candidates, _ = select_candidates(graph, data, history, options)
    problem = build_problem(graph, data, history, COST, candidates)
    assert plan.n_candidates == len(candidates)
    assert plan.baseline_bytes_per_day == pytest.approx(
        evaluate(problem, problem.baseline), rel=1e-9, abs=1.0
    )

    previous = float("inf")
    for point in plan.curve:
        assert len(point.chosen) <= point.k
        assert set(point.chosen) <= set(candidates)
        assert set(point.to_table).isdisjoint(problem.baseline)
        assert set(point.to_view) <= problem.baseline
        assert point.objective_bytes_per_day == pytest.approx(
            brute_force(problem, point.k), rel=1e-6, abs=1.0
        )
        assert point.objective_bytes_per_day <= previous * (1 + 1e-9) + 1.0
        previous = point.objective_bytes_per_day
        if point.greedy_objective_bytes_per_day is not None:
            assert point.greedy_objective_bytes_per_day >= point.objective_bytes_per_day - 1.0

    chosen = {r.uid for r in plan.recommendations}
    assert chosen == set(plan.curve[plan.recommended_k].chosen)
    if direction is Direction.VIEWS:
        assert all(u not in problem.baseline for u in chosen)
    if direction is Direction.TABLES:
        assert all(u in problem.baseline for u in chosen)


@settings(max_examples=EXAMPLES, deadline=None)
@given(projects())
def test_today_is_the_dry_run_bill(project):
    """Fitting kappa must reproduce the dry-run formula exactly at today's setup."""
    graph, data, history = project
    options = OptimizeOptions()
    candidates, _ = select_candidates(graph, data, history, options)
    problem = build_problem(graph, data, history, COST, candidates)
    assert evaluate(problem, problem.baseline) == pytest.approx(
        naive_baseline(problem), rel=1e-9, abs=1.0
    )
