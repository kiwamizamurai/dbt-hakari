"""`hakari report` (where the bill goes) and `hakari explain` (why a model is worth changing)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from dbt_hakari import cli
from dbt_hakari.breakdown import breakdown, kind_of
from dbt_hakari.cost import BigQueryOnDemand
from dbt_hakari.costdata import CostData, NodeCost
from dbt_hakari.errors import UsageError
from dbt_hakari.explain import explain, resolve
from dbt_hakari.graph import Graph, NodeKind
from dbt_hakari.history import ExternalLoad, History
from dbt_hakari.optimize import Change, OptimizeOptions
from dbt_hakari.optimize.problem import evaluate
from dbt_hakari.optimize.sweep import prepare
from tests.helpers import graph_to_manifest, node
from tests.world import build_world

MIB = 1024**2
COST = BigQueryOnDemand()
runner = CliRunner()


def small_project() -> tuple[Graph, CostData]:
    """s (5 MiB) -> T (a 1 MiB table) -> two tests; s -> V (a view) -> one test."""
    nodes = [
        node("s", NodeKind.SOURCE),
        node("T", NodeKind.TABLE_MODEL, ("s",)),
        node("V", NodeKind.VIEW_MODEL, ("s",)),
        node("t1", NodeKind.TEST, ("T",)),
        node("t2", NodeKind.TEST, ("T",)),
        node("t3", NodeKind.TEST, ("V",)),
    ]
    graph = Graph({n.uid: n for n in nodes})
    cost = CostData(
        nodes={
            "T": NodeCost(uid="T", bytes_processed=5 * MIB, n_tables=1),
            "V": NodeCost(uid="V", bytes_processed=5 * MIB, n_tables=1),
            "t1": NodeCost(uid="t1", bytes_processed=MIB, n_tables=1),
            "t2": NodeCost(uid="t2", bytes_processed=2 * MIB, n_tables=1),  # not identical to t1
            "t3": NodeCost(uid="t3", bytes_processed=MIB, n_tables=1),
        },
        leaf_bytes={"s": 5 * MIB, "T": MIB},
    )
    return graph, cost


def prepared(history: History | None = None):
    graph, cost = small_project()
    history = history or History(lookback_days=14, external_known=True)
    options = OptimizeOptions(assume_daily=True)
    return graph, cost, prepare(graph, cost, history, COST, options)


# -- report ----------------------------------------------------------------------------------


def test_the_breakdown_adds_up_to_the_bill_the_optimizer_starts_from():
    _, _, p = prepared()
    result = breakdown(p.problem, COST)
    assert result.bytes_per_day == pytest.approx(evaluate(p.problem, p.problem.baseline))
    assert sum(result.bytes_per_day_by_kind.values()) == pytest.approx(result.bytes_per_day)
    assert sum(r.share for r in result.top) == pytest.approx(1.0)


def test_the_breakdown_is_ranked_and_cut_to_the_top():
    _, _, p = prepared()
    result = breakdown(p.problem, COST, top=2)
    assert len(result.top) == 2
    assert result.top[0].bytes_per_day >= result.top[1].bytes_per_day
    assert result.n_queries >= 3


def test_outside_readers_show_up_as_their_own_kind():
    history = History(lookback_days=14, external_known=True)
    history.external["T"] = ExternalLoad(uid="T", runs_per_day=4, bytes_processed=MIB)
    _, _, p = prepared(history)
    result = breakdown(p.problem, COST)
    assert result.bytes_per_day_by_kind["outside dbt"] > 0
    assert {kind_of(q) for q in p.problem.queries} >= {"outside dbt", "builds", "tests"}


def test_identical_tests_are_listed_once_with_a_count():
    graph, cost = small_project()
    cost.nodes["t2"] = NodeCost(uid="t2", bytes_processed=MIB, n_tables=1)  # same as t1
    p = prepare(
        graph,
        cost,
        History(lookback_days=14, external_known=True),
        COST,
        OptimizeOptions(assume_daily=True),
    )
    labels = [r.label for r in breakdown(p.problem, COST).top]
    assert any("(+1 identical)" in label for label in labels)


# -- explain ---------------------------------------------------------------------------------


def test_explaining_a_view_gives_the_saving_of_making_it_a_table():
    graph, cost, p = prepared()
    e = explain(p, graph, cost, COST, "V")
    assert e.candidate and e.kind == "view" and e.change is Change.TO_TABLE
    flipped = evaluate(p.problem, p.problem.tables_from_flips({"V"}))
    assert e.bytes_per_day_after == pytest.approx(flipped)
    assert e.saving_bytes_per_day == pytest.approx(e.bytes_per_day_now - flipped)
    assert e.table_size_estimated
    assert any("estimated" in n for n in e.notes)


def test_explaining_a_table_nobody_else_reads_says_what_a_view_would_cost():
    graph, cost, p = prepared()
    e = explain(p, graph, cost, COST, "T")
    assert e.candidate and e.change is Change.TO_VIEW
    assert not e.table_size_estimated
    assert any("recompute" in n for n in e.notes)
    assert {r.label for r in e.readers} >= {"T", "t1"}  # its build and a test


def test_a_model_that_cannot_change_says_why():
    graph, cost, p = prepared(History(lookback_days=14))  # readers of tables unknown
    e = explain(p, graph, cost, COST, "T")
    assert not e.candidate and "unknown" in (e.why_not or "")
    source = explain(p, graph, cost, COST, "s")
    assert not source.candidate and "not a view or a table model" in (source.why_not or "")
    assert source.saving_bytes_per_day is None


def test_names_resolve_to_one_node_or_say_what_to_use():
    graph, _ = small_project()
    assert resolve(graph, "V") == "V"
    with pytest.raises(UsageError, match="no model named 'Q'"):
        resolve(graph, "Q")
    with pytest.raises(UsageError, match="Did you mean"):
        resolve(graph, "t")


def test_two_models_with_one_name_are_ambiguous():
    nodes = [
        node("a.m", NodeKind.VIEW_MODEL),
        node("b.m", NodeKind.VIEW_MODEL),
    ]
    graph = Graph({n.uid: n.__class__(**{**n.__dict__, "name": "m"}) for n in nodes})
    with pytest.raises(UsageError, match="ambiguous"):
        resolve(graph, "m")


# -- the commands ----------------------------------------------------------------------------


def cli_project(tmp_path, monkeypatch):
    graph, backend = build_world()
    manifest = tmp_path / "target" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps(graph_to_manifest(graph)))
    monkeypatch.setattr(cli.deps, "backend_factory", lambda settings: backend)
    monkeypatch.chdir(tmp_path)
    args = ["--manifest", str(manifest), "--data-dir", str(tmp_path / ".hakari")]
    assert runner.invoke(cli.app, ["collect", *args, "--location", "US"]).exit_code == 0
    return args


def test_report_prints_the_money_and_the_ranking(tmp_path, monkeypatch):
    args = cli_project(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["report", *args])
    assert result.exit_code == 0, result.output
    assert "USD/month" in result.output and "tests" in result.output


def test_report_json_is_a_document_on_stdout(tmp_path, monkeypatch):
    args = cli_project(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["report", *args, "--format", "json", "--top", "3"])
    document = json.loads(result.stdout)
    assert document["schema_version"] == 1 and len(document["top"]) <= 3
    assert document["bytes_per_day"] > 0


def test_report_markdown_has_tables(tmp_path, monkeypatch):
    args = cli_project(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["report", *args, "--format", "markdown"])
    assert (
        "### dbt-hakari report" in result.stdout and "| kind | GiB/day | share |" in result.stdout
    )


def test_explain_the_view_of_the_world(tmp_path, monkeypatch):
    args = cli_project(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["explain", "V", *args])
    assert result.exit_code == 0, result.output
    assert "make a table" in result.output and "saves" in result.output


def test_explain_json_and_markdown(tmp_path, monkeypatch):
    args = cli_project(tmp_path, monkeypatch)
    as_json = json.loads(runner.invoke(cli.app, ["explain", "V", *args, "--format", "json"]).stdout)
    assert as_json["candidate"] is True and as_json["change"] == "table"
    as_md = runner.invoke(cli.app, ["explain", "V", *args, "--format", "markdown"]).stdout
    assert "### dbt-hakari explain" in as_md


def test_explain_an_unknown_model_is_a_usage_error(tmp_path, monkeypatch):
    args = cli_project(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["explain", "nope", *args])
    assert result.exit_code == 2 and "no model named" in result.output


def test_explain_reports_whether_the_last_plan_includes_the_model(tmp_path, monkeypatch):
    args = cli_project(tmp_path, monkeypatch)
    runner.invoke(cli.app, ["optimize", *args, "--assume-daily", "--max-k", "1"])
    result = runner.invoke(cli.app, ["explain", "V", *args, "--format", "json"])
    assert json.loads(result.stdout)["in_last_plan"] is True


def test_report_without_collected_data_says_to_collect(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    graph, _ = build_world()
    (tmp_path / "m.json").write_text(json.dumps(graph_to_manifest(graph)))
    result = runner.invoke(cli.app, ["report", "--manifest", "m.json"])
    assert result.exit_code == 2 and "collect" in result.output
