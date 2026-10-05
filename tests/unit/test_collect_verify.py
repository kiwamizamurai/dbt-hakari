from __future__ import annotations

from dataclasses import replace

from dbt_hakari.backends import JobRecord
from dbt_hakari.collect import collect
from dbt_hakari.cost import BigQueryOnDemand
from dbt_hakari.graph import FLAG_READS_INFORMATION_SCHEMA, Graph
from dbt_hakari.history import build_history
from dbt_hakari.verify import CheckStatus, GateThresholds, Verdict, verify
from tests.world import MIB, build_world

COST = BigQueryOnDemand()


def run(graph, backend, **kw):
    cost_data = collect(graph, backend, threads=2)
    history = build_history(backend.list_jobs(14), 14)
    return cost_data, history, verify(graph, cost_data, history, COST, **kw)


def test_collect_measures_every_query_and_leaf():
    graph, backend = build_world(n_tests=4)
    cost_data = collect(graph, backend, threads=2)
    assert set(cost_data.nodes) == {"V", "t0", "t1", "t2", "t3"}
    assert cost_data.nodes["t0"].n_tables == 3
    assert cost_data.nodes["V"].bytes_processed == 2 * MIB
    assert cost_data.leaf_weight == {}  # every leaf expands to exactly one table


def test_collect_records_dry_run_errors_instead_of_failing():
    from dbt_hakari.backends import FakeBackend
    from dbt_hakari.errors import BackendError

    graph, _ = build_world(n_tests=1)

    def boom(sql: str):
        raise BackendError("syntax error")

    cost_data = collect(graph, FakeBackend(boom), threads=1)
    assert cost_data.nodes["t0"].error == "syntax error"


def test_matching_world_passes():
    graph, backend = build_world()
    _, _, report = run(graph, backend)
    assert report.verdict is Verdict.PASS
    assert report.graph_match_rate == 1.0
    assert report.n_bill_samples == 25
    assert report.bill_within_tol_rate == 1.0
    assert report.underestimate_rate == 0.0


def test_a_broken_billing_formula_fails():
    # the "bill" is 5 MiB per table instead of 10 MiB: the model would be wrong
    graph, backend = build_world(billed_per_table=5 * MIB)
    _, _, report = run(graph, backend)
    assert report.verdict is Verdict.FAIL
    assert report.bill_within_tol_rate == 0.0
    assert report.overestimate_rate == 1.0


def test_underestimates_are_reported_separately():
    graph, backend = build_world(billed_per_table=20 * MIB)
    _, _, report = run(graph, backend)
    assert report.verdict is Verdict.FAIL
    assert report.underestimate_rate == 1.0
    assert report.overestimate_rate == 0.0
    assert any("billed more than the formula predicts" in note for note in report.notes)


def test_cache_hits_failed_jobs_and_scripts_are_ignored():
    graph, backend = build_world(n_tests=25)
    junk = [
        JobRecord("t0", 0, cache_hit=True),
        JobRecord("t0", 999 * MIB, error=True),
        JobRecord("t0", 999 * MIB, statement_type="SCRIPT"),
        JobRecord(None, 999 * MIB),
    ]
    backend._jobs.extend(junk)
    _, history, report = run(graph, backend)
    assert history.n_cache_hits == 1
    assert history.n_unattributed == 1
    assert report.verdict is Verdict.PASS
    assert history.nodes["t0"].n_jobs == 3


def test_no_history_gives_a_structural_only_warning():
    graph, backend = build_world()
    backend._jobs.clear()
    _, _, report = run(graph, backend)
    assert report.verdict is Verdict.WARN
    assert report.n_bill_samples == 0
    assert any("could not be verified" in n for n in report.notes)


def test_few_samples_warn_with_low_confidence():
    graph, backend = build_world(n_tests=8)
    _, _, report = run(graph, backend)
    assert report.verdict is Verdict.WARN
    assert any("low confidence" in n for n in report.notes)


def test_reservation_jobs_fail_the_gate():
    graph, backend = build_world(reservation_id="proj:US.my-reservation")
    _, _, report = run(graph, backend)
    assert report.verdict is Verdict.FAIL
    assert any("reservation" in n for n in report.notes)


def test_graph_mismatch_excludes_the_node_and_explains_why():
    graph, backend = build_world()
    nodes = dict(graph.nodes)
    nodes["V"] = replace(
        nodes["V"],
        flags=frozenset({FLAG_READS_INFORMATION_SCHEMA}),
        parents=("s0", "s1"),  # the graph undercounts: BigQuery will say 3 tables
    )
    cost_data, history, report = None, None, None
    graph = Graph(nodes)
    cost_data = collect(graph, backend, threads=2)
    history = build_history(backend.list_jobs(14), 14)
    report = verify(graph, cost_data, history, COST)
    assert report.checks["V"].status is CheckStatus.TABLE_MISMATCH
    assert "INFORMATION_SCHEMA" in report.excluded["V"]
    assert report.graph_match_rate < 1.0


def test_thresholds_are_configurable():
    graph, backend = build_world(billed_per_table=int(10.4 * MIB))
    _, _, strict = run(graph, backend, thresholds=GateThresholds(tolerance=0.01))
    _, _, loose = run(graph, backend, thresholds=GateThresholds(tolerance=0.05))
    assert strict.bill_within_tol_rate == 0.0
    assert loose.bill_within_tol_rate == 1.0
