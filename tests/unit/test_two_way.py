"""Tables can become views, views can become tables, and the bill follows the bytes."""

from __future__ import annotations

import pytest

from dbt_hakari.backends.base import JobRecord
from dbt_hakari.cost import BigQueryOnDemand
from dbt_hakari.costdata import CostData, NodeCost
from dbt_hakari.graph import Graph, NodeKind
from dbt_hakari.history import ExternalLoad, History, build_history
from dbt_hakari.optimize import Change, Direction, OptimizeOptions, optimize
from dbt_hakari.optimize.plan import KPoint
from dbt_hakari.optimize.problem import Candidate, Problem, Query
from dbt_hakari.optimize.solvers.scipy_highs import ScipyHighs
from dbt_hakari.optimize.sweep import recommend
from tests.helpers import node

MIB = 1024**2
COST = BigQueryOnDemand()
SOLVER = ScipyHighs()


def table_over_source() -> tuple[Graph, CostData]:
    """s (5 MiB) -> T (a table of 1 MiB) -> one test reading T."""
    nodes = [
        node("s", NodeKind.SOURCE),
        node("T", NodeKind.TABLE_MODEL, ("s",)),
        node("t", NodeKind.TEST, ("T",)),
    ]
    graph = Graph({n.uid: n for n in nodes})
    cost = CostData(
        nodes={
            "T": NodeCost(uid="T", bytes_processed=5 * MIB, n_tables=1),
            "t": NodeCost(uid="t", bytes_processed=1 * MIB, n_tables=1),
        },
        leaf_bytes={"s": 5 * MIB, "T": 1 * MIB},
    )
    return graph, cost


def run(graph, cost, history, **options):
    options.setdefault("assume_daily", True)
    return optimize(graph, cost, history, COST, OptimizeOptions(**options), solver=SOLVER)


def known_readers() -> History:
    return History(lookback_days=14, external_known=True)


def test_a_table_nobody_else_reads_becomes_a_view_when_that_is_cheaper():
    graph, cost = table_over_source()
    plan = run(graph, cost, known_readers())
    # as a table: the build (10 MiB minimum) plus the test (10 MiB); as a view: only the test
    assert plan.baseline_bytes_per_day == 20 * MIB
    assert plan.curve[1].objective_bytes_per_day == 10 * MIB
    assert [(r.uid, r.change) for r in plan.recommendations] == [("T", Change.TO_VIEW)]


def test_other_readers_keep_a_table_a_table():
    graph, cost = table_over_source()
    history = known_readers()
    history.external["T"] = ExternalLoad(uid="T", runs_per_day=50, bytes_processed=100 * MIB)
    plan = run(graph, cost, history)
    assert plan.recommendations == []
    assert plan.curve[-1].objective_bytes_per_day == plan.baseline_bytes_per_day


def test_a_table_is_left_alone_when_its_readers_are_unknown():
    graph, cost = table_over_source()
    plan = run(graph, cost, None)
    assert plan.n_candidates == 0
    assert "unknown" in plan.excluded["T"]


def test_a_table_read_by_an_exposure_is_left_alone():
    graph, cost = table_over_source()
    graph = Graph(graph.nodes, exposed=frozenset({"T"}))
    plan = run(graph, cost, known_readers())
    assert "exposure" in plan.excluded["T"]


def test_direction_limits_what_may_change():
    graph, cost = table_over_source()
    plan = run(graph, cost, known_readers(), direction=Direction.VIEWS)
    assert plan.n_candidates == 0 and plan.excluded == {}


def test_reads_are_proportional_so_a_pruned_query_stays_cheap():
    """A test that reads 1 MiB of a 5 MiB table reads a fifth of it, however it is reached."""
    graph, _ = table_over_source()
    problem = Problem(
        graph,
        {"T": Candidate("T", True, 1.0 * MIB)},
        {"s": 5.0 * MIB},
        {},
        [Query("t", ("T",), 1.0, 1.0 * MIB, 1.0)],
        COST,
    )
    assert problem.fit_kappa(("T",), 1.0 * MIB) == 1.0
    assert problem.fit_kappa(("s",), 1.0 * MIB) == pytest.approx(0.2)


def test_the_baseline_reproduces_the_dry_run_bill_of_every_query():
    """kappa is fitted so that the bytes add up at today's setup: the calibration invariant."""
    graph, cost = table_over_source()
    plan = run(graph, cost, known_readers())
    # the legacy formula on the dry-run totals: build max(5, 10, 10) + test max(1, 10, 10)
    assert plan.baseline_bytes_per_day == (10 + 10) * MIB


def test_the_minimum_applies_to_the_query_as_a_whole_not_per_table():
    """Measured on BigQuery: a 30.5 MiB column plus a tiny table was billed 31 MiB, and 1.5 MiB
    read from one table was billed 10 MiB."""
    assert COST.billed_bytes(30.5 * MIB, 2) == 30.5 * MIB
    assert COST.billed_bytes(1.5 * MIB, 1) == 10 * MIB
    assert COST.billed_bytes(0, 3) == 30 * MIB


def point(k: int, objective: float) -> KPoint:
    return KPoint(k=k, objective_bytes_per_day=objective, saving_pct=0.0, chosen=())


def test_changes_that_pay_off_only_together_are_recommended():
    # no single change helps, a pair saves 17%: stopping at the first flat step would say "none"
    curve = [point(0, 290.0), point(1, 290.0), point(2, 240.0), point(3, 240.0)]
    assert recommend(curve, 290.0) == 2


def test_nothing_is_recommended_when_even_the_best_saves_almost_nothing():
    curve = [point(0, 100.0), point(1, 99.5), point(2, 99.0)]
    assert recommend(curve, 100.0) == 0


def test_the_smallest_number_of_changes_within_two_percent_of_the_best_is_chosen():
    curve = [point(0, 100.0), point(1, 70.0), point(2, 60.0), point(3, 59.0), point(4, 58.5)]
    assert recommend(curve, 100.0) == 2  # 60.0 is within 2.0 of the best, 58.5


def test_queries_other_people_run_are_attributed_to_the_relations_they_read():
    jobs = [
        JobRecord(None, 50 * MIB, from_dbt=False, processed_bytes=40 * MIB, referenced=("p.d.t",)),
        JobRecord(None, 50 * MIB, from_dbt=False, processed_bytes=60 * MIB, referenced=("p.d.t",)),
        JobRecord(None, 0, from_dbt=False, cache_hit=True, referenced=("p.d.t",)),
        JobRecord(None, 50 * MIB, from_dbt=False, referenced=("p.other.x",)),
        JobRecord("model.x.t", 10 * MIB, creation_day="2026-01-01"),
    ]
    history = build_history(jobs, 14, {"p.d.t": "model.x.t"})
    assert history.external_known
    load = history.external["model.x.t"]
    assert load.runs_per_day == pytest.approx(2 / 14)
    assert load.bytes_processed == 50 * MIB  # the median of 40 and 60
    assert set(history.external) == {"model.x.t"}


def test_without_a_relation_index_no_reader_is_known():
    history = build_history([JobRecord(None, 1, from_dbt=False, referenced=("p.d.t",))], 14)
    assert not history.external_known and history.external == {}


def expensive_table(build_mib: int, events_mib: int = 3072) -> tuple[Graph, CostData]:
    """events -> sessions (a 200 MiB table, built from a scan of build_mib) -> 3 readers, each of
    which only needs one column of it (30 MiB)."""
    nodes = [
        node("events", NodeKind.SOURCE),
        node("sessions", NodeKind.TABLE_MODEL, ("events",)),
        *[node(f"t{i}", NodeKind.TEST, ("sessions",)) for i in range(3)],
    ]
    graph = Graph({n.uid: n for n in nodes})
    cost = CostData(
        nodes={
            "sessions": NodeCost(uid="sessions", bytes_processed=build_mib * MIB, n_tables=1),
            **{
                f"t{i}": NodeCost(uid=f"t{i}", bytes_processed=30 * MIB, n_tables=1)
                for i in range(3)
            },
        },
        leaf_bytes={"events": events_mib * MIB, "sessions": 200 * MIB},
    )
    return graph, cost


def test_a_table_that_is_expensive_to_build_is_not_turned_into_a_view():
    # as a view every read recomputes the 2.5 GiB scan, however little of the result it needs
    graph, cost = expensive_table(build_mib=2560)
    plan = run(graph, cost, known_readers())
    assert plan.recommendations == []
    assert plan.curve[-1].objective_bytes_per_day == plan.baseline_bytes_per_day


def test_a_table_that_is_cheap_to_build_may_become_a_view():
    graph, cost = expensive_table(build_mib=12, events_mib=20)
    plan = run(graph, cost, known_readers())
    assert [(r.uid, r.change) for r in plan.recommendations] == [("sessions", Change.TO_VIEW)]


def test_the_plan_says_what_it_means_in_money():
    from dbt_hakari.report import summary_lines

    graph, cost = table_over_source()
    plan = run(graph, cost, known_readers())
    assert plan.price_per_month_now == pytest.approx(COST.price(30 * 20 * MIB))
    assert plan.price_per_month_after == pytest.approx(COST.price(30 * 10 * MIB))
    text = "\n".join(summary_lines(plan))
    assert "USD/month" in text and "free" in text
    assert "small in money terms" in text  # a few MiB a day is nothing


def test_nothing_to_change_is_said_plainly():
    from dbt_hakari.report import summary_lines

    graph, cost = table_over_source()
    history = known_readers()
    history.external["T"] = ExternalLoad(uid="T", runs_per_day=50, bytes_processed=100 * MIB)
    text = "\n".join(summary_lines(run(graph, cost, history)))
    assert "recommended: no change" in text


def test_a_table_to_view_recommendation_carries_the_caution():
    graph, cost = table_over_source()
    plan = run(graph, cost, known_readers())
    assert any("change one table, compare the next days' bill" in n for n in plan.notes)


def test_no_caution_when_no_table_becomes_a_view():
    graph, cost = table_over_source()
    history = known_readers()
    history.external["T"] = ExternalLoad(uid="T", runs_per_day=50, bytes_processed=100 * MIB)
    plan = run(graph, cost, history)
    assert not any("compare the next days' bill" in n for n in plan.notes)


def test_the_part_of_the_bill_that_comes_from_outside_dbt_is_shown():
    from dbt_hakari.report import summary_lines

    graph, cost = table_over_source()
    history = known_readers()
    history.external["T"] = ExternalLoad(uid="T", runs_per_day=2, bytes_processed=1 * MIB)
    plan = run(graph, cost, history)
    assert plan.external_bytes_per_day == pytest.approx(2 * 10 * MIB)  # 2 runs x the 10 MiB minimum
    assert any("queries from outside dbt" in line for line in summary_lines(plan))


def test_bracketed_words_in_notes_are_printed_not_swallowed():
    from rich.console import Console

    from dbt_hakari.report import render_verification
    from dbt_hakari.verify import VerificationReport

    console = Console(width=200, record=True)
    report = VerificationReport(notes=["give --output-ratio or [sizes] if tables are smaller"])
    render_verification(console, report)
    assert "[sizes]" in console.export_text()


def test_changes_that_only_work_together_are_not_called_one_change():
    from dbt_hakari.optimize.plan import Plan, Recommendation
    from dbt_hakari.report import summary_lines

    def plan(savings):
        return Plan(
            baseline_bytes_per_day=100 * MIB,
            curve=[
                KPoint(k=0, objective_bytes_per_day=100.0 * MIB, saving_pct=0.0, chosen=()),
                KPoint(k=2, objective_bytes_per_day=80.0 * MIB, saving_pct=20.0, chosen=("a", "b")),
            ],
            recommended_k=1,
            recommendations=[
                Recommendation(uid=u, name=u, change=Change.TO_TABLE, saving_bytes_per_day=s * MIB)
                for u, s in zip("ab", savings, strict=True)
            ],
            excluded={},
            assumptions=[],
            assume_daily=True,
            verification_verdict=None,
            n_candidates=2,
            n_queries=1,
        )

    together = "\n".join(summary_lines(plan([20.0, 20.0])))  # each alone "saves" the whole gain
    assert "one change" not in together
    lopsided = "\n".join(summary_lines(plan([18.0, 2.0])))
    assert "90% of the saving comes from one change: a" in lopsided
