"""The MILP must agree with exhaustive search on random problems.

Candidates go both ways (views that could become tables, tables that could become views); queries
read tables in proportion (kappa) or with a constant total.
"""

from __future__ import annotations

import os
from itertools import combinations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dbt_hakari.cost import BigQueryOnDemand
from dbt_hakari.graph import Graph, Node, NodeKind
from dbt_hakari.optimize.builder import build_model
from dbt_hakari.optimize.problem import Candidate, Problem, Query, evaluate
from dbt_hakari.optimize.solvers.base import SolveStatus
from dbt_hakari.optimize.solvers.scipy_highs import ScipyHighs

MIB = 1024**2
COST = BigQueryOnDemand()
SOLVER = ScipyHighs()
EXAMPLES = int(os.environ.get("HAKARI_FUZZ", "150"))


@st.composite
def problems(draw):
    nodes: list[Node] = []
    pool: list[str] = []
    n_sources = draw(st.integers(1, 4))
    for i in range(n_sources):
        nodes.append(Node(f"s{i}", f"s{i}", NodeKind.SOURCE))
        pool.append(f"s{i}")

    def parents_of():
        return tuple(draw(st.lists(st.sampled_from(pool), min_size=1, max_size=3, unique=True)))

    candidates: dict[str, Candidate] = {}
    sizes = {f"s{i}": float(draw(st.integers(0, 80)) * MIB) for i in range(n_sources)}
    for i in range(draw(st.integers(1, 6))):
        kind = draw(
            st.sampled_from(
                [NodeKind.VIEW_MODEL] * 3 + [NodeKind.TABLE_MODEL] * 2 + [NodeKind.EPHEMERAL_MODEL]
            )
        )
        uid = f"m{i}"
        nodes.append(Node(uid, uid, kind, parents=parents_of()))
        pool.append(uid)
        size = float(draw(st.integers(0, 80)) * MIB)
        if kind is NodeKind.VIEW_MODEL and draw(st.booleans()):
            candidates[uid] = Candidate(uid, False, size)
        elif kind is NodeKind.TABLE_MODEL and draw(st.booleans()):
            floor = float(draw(st.integers(0, 80)) * MIB)
            candidates[uid] = Candidate(uid, True, size, floor)
        elif kind is NodeKind.TABLE_MODEL:
            sizes[uid] = size  # a fixed table
    for i in range(draw(st.integers(0, 2))):
        uid = f"i{i}"
        nodes.append(Node(uid, uid, NodeKind.INCREMENTAL_MODEL, parents=parents_of()))
        pool.append(uid)
        sizes[uid] = float(draw(st.integers(0, 80)) * MIB)
    graph = Graph({n.uid: n for n in nodes})

    leaf_weight = {f"s{i}": draw(st.integers(1, 3)) for i in range(n_sources)}
    queries: list[Query] = []

    def kappa():
        return draw(st.one_of(st.none(), st.floats(0.0, 1.5)))

    for i in range(draw(st.integers(1, 5))):
        queries.append(
            Query(
                f"t{i}",
                parents_of(),
                float(draw(st.integers(1, 3))),
                float(draw(st.integers(0, 60)) * MIB),
                kappa(),
            )
        )
    for n in sorted(candidates):
        queries.append(
            Query(
                f"build:{n}",
                graph.nodes[n].parents,
                float(draw(st.integers(1, 2))),
                float(draw(st.integers(0, 60)) * MIB),
                kappa(),
                build_of=n,
            )
        )
    return Problem(graph, candidates, sizes, leaf_weight, queries, COST)


def exhaustive(problem, k=None, force_table=(), force_view=()):
    names = sorted(problem.candidates)
    best = float("inf")
    for size in range(len(names) + 1):
        for subset in combinations(names, size):
            tables = frozenset(subset)
            if k is not None and len(problem.flips(tables)) > k:
                continue
            if not set(force_table) <= tables or set(force_view) & tables:
                continue
            best = min(best, evaluate(problem, tables))
    return best


def solve(problem, k=None, force_table=(), force_view=()):
    model = build_model(problem, max_changes=k, force_table=force_table, force_view=force_view)
    result = SOLVER.solve(model.ir, 30.0, 1e-9)
    assert result.status is SolveStatus.OPTIMAL
    return model.selection(result.values)


@settings(max_examples=EXAMPLES, deadline=None)
@given(problems(), st.integers(0, 5))
def test_milp_optimum_equals_exhaustive_search(problem, k):
    chosen = solve(problem, k)
    assert len(problem.flips(chosen)) <= k
    achieved = evaluate(problem, chosen)
    assert achieved == pytest.approx(exhaustive(problem, k), rel=1e-6, abs=1.0)


@settings(max_examples=EXAMPLES, deadline=None)
@given(problems())
def test_unconstrained_optimum_equals_exhaustive_search(problem):
    chosen = solve(problem)
    assert evaluate(problem, chosen) == pytest.approx(exhaustive(problem), rel=1e-6, abs=1.0)


@settings(max_examples=max(30, EXAMPLES // 2), deadline=None)
@given(problems(), st.data())
def test_forced_choices_are_respected_and_optimal(problem, data):
    names = sorted(problem.candidates)
    if len(names) < 2:
        return
    forced_table = data.draw(st.sampled_from(names))
    forced_view = data.draw(st.sampled_from([n for n in names if n != forced_table]))
    chosen = solve(problem, None, [forced_table], [forced_view])
    assert forced_table in chosen and forced_view not in chosen
    assert evaluate(problem, chosen) == pytest.approx(
        exhaustive(problem, None, [forced_table], [forced_view]), rel=1e-6, abs=1.0
    )


@settings(max_examples=max(30, EXAMPLES // 2), deadline=None)
@given(problems())
def test_the_baseline_is_always_feasible_at_zero_changes(problem):
    chosen = solve(problem, 0)
    assert chosen == problem.baseline
