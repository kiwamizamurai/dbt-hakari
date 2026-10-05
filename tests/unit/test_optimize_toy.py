from __future__ import annotations

from dbt_hakari.cost import BigQueryOnDemand
from dbt_hakari.costdata import CostData, NodeCost
from dbt_hakari.optimize import OptimizeOptions, optimize
from dbt_hakari.optimize.solvers.scipy_highs import ScipyHighs
from tests.helpers import toy_graph

MIB = 1024**2
SOLVER = ScipyHighs()
COST = BigQueryOnDemand()


def cost_data_for(graph, bytes_per_node=MIB):
    nodes = {}
    for uid, n in graph.nodes.items():
        if n.kind.value in ("view_model", "test", "table_model"):
            nodes[uid] = NodeCost(
                uid=uid, bytes_processed=bytes_per_node, n_tables=graph.table_count(uid)
            )
    return CostData(nodes=nodes)


def plan_for(n_tests, n_tables):
    graph = toy_graph(n_tests, n_tables)
    options = OptimizeOptions(max_k=2, assume_daily=True)
    return optimize(graph, cost_data_for(graph), None, COST, options, solver=SOLVER)


def test_three_tables_and_two_readers_pay_off():
    plan = plan_for(n_tests=2, n_tables=3)
    # as a view: 2 x 30 MiB = 60; as a table: build 30 + 2 x 10 = 50
    assert plan.baseline_bytes_per_day == 60 * MIB
    assert plan.curve[1].chosen == ("V",)
    assert plan.curve[1].objective_bytes_per_day == 50 * MIB
    assert plan.recommended_k == 1


def test_two_tables_need_three_readers():
    # T=2: n=2 ties (40 vs 20 + 20), so the view is left alone; n=3 wins (60 vs 20 + 30)
    assert plan_for(n_tests=2, n_tables=2).curve[1].chosen == ()
    plan = plan_for(n_tests=3, n_tables=2)
    assert plan.curve[1].chosen == ("V",)
    assert plan.curve[1].objective_bytes_per_day == 50 * MIB


def test_one_reader_never_pays_off():
    assert plan_for(n_tests=1, n_tables=5).curve[1].chosen == ()


def test_break_even_rule_of_thumb_holds_for_many_shapes():
    for tables in range(1, 7):
        for readers in range(1, 9):
            chosen = plan_for(readers, tables).curve[1].chosen
            assert (chosen == ("V",)) == (readers * tables > tables + readers), (tables, readers)


def test_forced_view_is_respected():
    graph = toy_graph(5, 3)
    plan = optimize(
        graph,
        cost_data_for(graph),
        None,
        COST,
        OptimizeOptions(max_k=1, assume_daily=True),
        solver=SOLVER,
        force_view=("V",),
    )
    assert plan.curve[1].chosen == ()


def test_excluded_views_are_reported_with_a_reason():
    graph = toy_graph(5, 3)
    plan = optimize(
        graph,
        cost_data_for(graph),
        None,
        COST,
        OptimizeOptions(max_k=1, assume_daily=True, exclude=("V",)),
        solver=SOLVER,
    )
    assert "exclude pattern" in plan.excluded["V"]
    assert plan.n_candidates == 0 and plan.curve[-1].chosen == ()
