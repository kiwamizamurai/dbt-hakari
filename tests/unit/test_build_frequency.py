"""A view that becomes a table is built as often as the project's table models are built."""

from __future__ import annotations

from dbt_hakari.cost import BigQueryOnDemand
from dbt_hakari.costdata import CostData, NodeCost
from dbt_hakari.graph import Graph, NodeKind
from dbt_hakari.history import History, NodeHistory
from dbt_hakari.optimize.problem import Candidate
from dbt_hakari.optimize.queries import build_frequency, build_problem
from tests.helpers import node


def project() -> Graph:
    nodes = [
        node("s", NodeKind.SOURCE),
        node("V", NodeKind.VIEW_MODEL, ("s",)),
        node("T1", NodeKind.TABLE_MODEL, ("V",)),
        node("T2", NodeKind.TABLE_MODEL, ("V",)),
    ]
    return Graph({n.uid: n for n in nodes})


def history(rate_t1: float, rate_t2: float) -> History:
    return History(
        lookback_days=14,
        nodes={
            "T1": NodeHistory(uid="T1", runs_per_day=rate_t1, billed_p50=10, n_jobs=1),
            "T2": NodeHistory(uid="T2", runs_per_day=rate_t2, billed_p50=10, n_jobs=1),
        },
    )


def test_it_follows_how_often_the_table_models_run():
    assert build_frequency(project(), history(4.0, 4.0), assume_daily=False) == 4.0


def test_it_is_the_median_so_one_odd_model_does_not_decide():
    graph = project()
    assert build_frequency(graph, history(1.0, 3.0), assume_daily=False) == 2.0


def test_without_history_or_with_assume_daily_it_is_once_a_day():
    assert build_frequency(project(), None, assume_daily=False) == 1.0
    assert build_frequency(project(), history(4.0, 4.0), assume_daily=True) == 1.0


def test_the_build_query_carries_that_frequency():
    graph = project()
    cost = CostData(
        nodes={uid: NodeCost(uid=uid, bytes_processed=1, n_tables=1) for uid in ("V", "T1", "T2")}
    )
    problem = build_problem(
        graph, cost, history(4.0, 4.0), BigQueryOnDemand(), {"V": Candidate("V", False, 1.0)}
    )
    build = next(q for q in problem.queries if q.build_of == "V")
    assert build.weight == 4.0
