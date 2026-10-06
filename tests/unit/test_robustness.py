"""First-use mistakes must end in a sentence that says what to do, never in a stack trace."""

from __future__ import annotations

import json
import sys
import types

import pytest
from typer.testing import CliRunner

from dbt_hakari import cli
from dbt_hakari.backends import FakeBackend
from dbt_hakari.backends.base import JobRecord
from dbt_hakari.cost import BigQueryOnDemand
from dbt_hakari.costdata import CostData, NodeCost
from dbt_hakari.dbt.manifest import load_manifest_with_warnings
from dbt_hakari.dbt.selectors import matches, matching_pattern, validate
from dbt_hakari.errors import BackendError, ManifestError, SolverFailed, UsageError
from dbt_hakari.graph import Graph, Node, NodeKind
from dbt_hakari.history import build_history
from dbt_hakari.optimize import OptimizeOptions, optimize
from dbt_hakari.optimize.solvers.base import SolveResult, SolveStatus
from dbt_hakari.store import DataStore
from tests.helpers import graph_to_manifest, toy_graph
from tests.world import build_world

runner = CliRunner()
MIB = 1024**2


def project(tmp_path, monkeypatch, backend=None, **world):
    graph, fake = build_world(**world)
    manifest = tmp_path / "target" / "manifest.json"
    manifest.parent.mkdir(exist_ok=True)
    manifest.write_text(json.dumps(graph_to_manifest(graph)))
    monkeypatch.setattr(cli.deps, "backend_factory", lambda settings: backend or fake)
    monkeypatch.chdir(tmp_path)
    return ["--manifest", str(manifest), "--data-dir", str(tmp_path / ".hakari")]


# -- settings reach the commands -------------------------------------------------------------


def test_settings_in_hakari_toml_are_used_by_collect(tmp_path, monkeypatch):
    args = project(tmp_path, monkeypatch)
    (tmp_path / "hakari.toml").write_text("[bigquery]\nlookback_days = 7\nthreads = 2\n")
    assert runner.invoke(cli.app, ["collect", *args, "--location", "US"]).exit_code == 0
    assert DataStore(tmp_path / ".hakari").load_history().lookback_days == 7


def test_a_command_line_option_beats_the_config_file(tmp_path, monkeypatch):
    args = project(tmp_path, monkeypatch)
    (tmp_path / "hakari.toml").write_text("[bigquery]\nlookback_days = 7\n")
    result = runner.invoke(cli.app, ["collect", *args, "--location", "US", "--lookback-days", "3"])
    assert result.exit_code == 0
    assert DataStore(tmp_path / ".hakari").load_history().lookback_days == 3


def test_assume_daily_in_the_config_is_not_overwritten_by_the_default(tmp_path, monkeypatch):
    args = project(tmp_path, monkeypatch)
    runner.invoke(cli.app, ["collect", *args, "--location", "US"])
    (tmp_path / "hakari.toml").write_text("[optimize]\nassume_daily = true\n")
    result = runner.invoke(cli.app, ["optimize", *args, "--format", "json"])
    assert result.exit_code == 0, result.output
    assert any("once a day" in n for n in json.loads(result.stdout)["plan"]["notes"])


def test_verify_accepts_the_user_options(tmp_path, monkeypatch):
    args = project(tmp_path, monkeypatch)
    runner.invoke(cli.app, ["collect", *args, "--location", "US"])
    result = runner.invoke(cli.app, ["verify", *args, "--all-users"])
    assert result.exit_code == 0, result.output


# -- mistakes that used to be stack traces ---------------------------------------------------


def test_a_manifest_without_models_says_so(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"nodes": {}}))
    with pytest.raises(ManifestError, match="no dbt models"):
        load_manifest_with_warnings(path)


def test_the_cli_reports_an_empty_manifest_with_exit_code_3(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "m.json").write_text(json.dumps({"nodes": {}}))
    result = runner.invoke(cli.app, ["collect", "--manifest", "m.json", "--location", "US"])
    assert result.exit_code == 3
    assert "no dbt models" in result.output


def test_a_solver_without_a_solution_is_a_clear_error():
    class Stuck:
        def solve(self, ir, time_limit, mip_rel_gap):
            return SolveResult(SolveStatus.TIME_LIMIT, None, None)

    graph = toy_graph(5, 3)
    cost = CostData(
        nodes={
            uid: NodeCost(uid=uid, bytes_processed=MIB, n_tables=graph.table_count(uid))
            for uid, n in graph.nodes.items()
            if n.kind is not NodeKind.SOURCE
        }
    )
    with pytest.raises(SolverFailed, match="time-limit") as error:
        optimize(
            graph,
            cost,
            None,
            BigQueryOnDemand(),
            OptimizeOptions(assume_daily=True),
            solver=Stuck(),
        )
    assert error.value.exit_code == 6


def test_missing_credentials_become_a_hint_not_a_traceback(monkeypatch):
    from google.cloud import bigquery

    def no_credentials(*args, **kwargs):
        raise RuntimeError("Your default credentials were not found")

    monkeypatch.setattr(bigquery, "Client", no_credentials)
    from dbt_hakari.backends.bigquery import BigQueryBackend

    with pytest.raises(BackendError, match="gcloud auth application-default login"):
        BigQueryBackend("p", "US")


def test_a_failing_history_query_is_a_backend_error(monkeypatch):
    from google.cloud import bigquery

    class Job:
        def result(self):
            raise RuntimeError("Access Denied")

    class Client:
        project = "p"

        def __init__(self, *a, **k):
            pass

        def query(self, *a, **k):
            return Job()

    monkeypatch.setattr(bigquery, "Client", Client)
    from dbt_hakari.backends.bigquery import BigQueryBackend

    with pytest.raises(BackendError, match="cannot read job history"):
        list(BigQueryBackend("p", "US").list_jobs(14))


def test_when_every_dry_run_fails_the_hint_is_about_setup(tmp_path, monkeypatch):
    def boom(sql):
        raise BackendError("Not found: Dataset was not found in location EU")

    args = project(tmp_path, monkeypatch, backend=FakeBackend(boom))
    result = runner.invoke(cli.app, ["collect", *args, "--location", "EU"])
    assert result.exit_code == 5
    assert "every dry-run failed" in result.output and "--location" in result.output


# -- saved files -----------------------------------------------------------------------------


def test_a_directory_in_place_of_a_data_file_is_a_usage_error(tmp_path):
    (tmp_path / "costdata.json").mkdir()
    with pytest.raises(UsageError, match="cannot read"):
        DataStore(tmp_path).load_cost_data()


def test_a_file_from_another_version_says_to_collect_again(tmp_path):
    (tmp_path / "history.json").write_text(json.dumps({"schema_version": 99, "lookback_days": 14}))
    with pytest.raises(UsageError, match="another version"):
        DataStore(tmp_path).load_history()


def test_every_saved_file_carries_a_schema_version(tmp_path, monkeypatch):
    args = project(tmp_path, monkeypatch)
    runner.invoke(cli.app, ["collect", *args, "--location", "US"])
    runner.invoke(cli.app, ["verify", *args])
    runner.invoke(cli.app, ["optimize", *args, "--assume-daily"])
    for name in ("costdata", "history", "verification", "plan"):
        saved = json.loads((tmp_path / ".hakari" / f"{name}.json").read_text())
        assert saved["schema_version"] == 1, name


# -- selectors -------------------------------------------------------------------------------


def test_every_selector_kind_matches_what_it_says():
    n = Node(
        "model.pkg.int_orders", "int_orders", NodeKind.VIEW_MODEL,
        tags=frozenset({"keep"}), path="models/intermediate/int_orders.sql", package="pkg",
    )  # fmt: skip
    for pattern in ("tag:keep", "path:models/intermediate/*", "uid:model.pkg.*", "name:int_*",
                    "package:pkg", "int_orders", "int_*"):  # fmt: skip
        assert matches(n, pattern), pattern
    for pattern in ("tag:other", "path:models/marts/*", "name:mart_*", "package:other", "mart_*"):
        assert not matches(n, pattern), pattern
    assert matching_pattern(n, ["tag:other", "name:int_*"]) == "name:int_*"


def test_a_typo_in_a_selector_prefix_is_an_error_not_a_silent_miss():
    with pytest.raises(UsageError, match="unknown selector 'tga:keep'"):
        validate(["name:ok", "tga:keep"])
    validate(["tag:a", "path:b/*", "uid:c", "name:d", "package:e", "plain_glob*"])


# -- history, relations, notes ---------------------------------------------------------------


def test_a_query_reading_many_relations_counts_as_one_job_for_its_user():
    job = JobRecord(
        None, 9, from_dbt=False, processed_bytes=MIB, user="a@x.test",
        referenced=("p.d.a", "p.d.b", "p.d.c"),
    )  # fmt: skip
    history = build_history([job], 14, {"p.d.a": "A", "p.d.b": "B", "p.d.c": "C"})
    assert history.users == {"a@x.test": 1}
    assert set(history.external) == {"A", "B", "C"}


def test_two_nodes_sharing_a_relation_are_reported_and_the_model_wins():
    nodes = [
        Node("src", "src", NodeKind.SOURCE, relation="`p`.`d`.`t`"),
        Node("m", "m", NodeKind.VIEW_MODEL, relation="`p`.`d`.`t`", parents=("src",)),
    ]
    graph = Graph({n.uid: n for n in nodes})
    assert graph.relation_index() == {"p.d.t": "m"}
    assert graph.duplicate_relations() == {
        "p.d.t": ["src", "m"]
    } or graph.duplicate_relations() == {"p.d.t": ["m", "src"]}


def test_queries_without_table_sizes_are_counted_in_a_note():
    graph = toy_graph(3, 3)
    cost = CostData(
        nodes={
            u: NodeCost(uid=u, bytes_processed=MIB, n_tables=graph.table_count(u))
            for u, n in graph.nodes.items()
            if n.kind is not NodeKind.SOURCE
        }
    )
    from dbt_hakari.optimize.solvers.scipy_highs import ScipyHighs

    plan = optimize(
        graph,
        cost,
        None,
        BigQueryOnDemand(),
        OptimizeOptions(assume_daily=True),
        solver=ScipyHighs(),
    )
    assert any("size is unknown" in n for n in plan.notes)


def test_main_module_runs_the_cli_only_when_executed(monkeypatch):
    called = []
    fake = types.ModuleType("dbt_hakari.cli")
    fake.app = lambda: called.append(1)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dbt_hakari.cli", fake)
    sys.modules.pop("dbt_hakari.__main__", None)
    import dbt_hakari.__main__  # noqa: F401

    assert called == []  # importing the module must not start the CLI


@pytest.mark.parametrize("location", ["US", "EU", "us", "asia-northeast1", "europe-west1"])
def test_every_documented_location_is_accepted(monkeypatch, location):
    from google.cloud import bigquery

    class Client:
        project = "p"

        def __init__(self, *a, **k):
            self.location = k["location"]

    monkeypatch.setattr(bigquery, "Client", Client)
    from dbt_hakari.backends.bigquery import BigQueryBackend

    assert BigQueryBackend("p", location).project == "p"


def test_a_location_with_odd_characters_is_refused(monkeypatch):
    from google.cloud import bigquery

    monkeypatch.setattr(bigquery, "Client", lambda *a, **k: None)
    from dbt_hakari.backends.bigquery import BigQueryBackend

    with pytest.raises(BackendError, match="invalid location"):
        BigQueryBackend("p", "US; DROP TABLE x")


def test_the_history_query_uses_a_lower_case_region_qualifier(monkeypatch):
    from google.cloud import bigquery

    seen = {}

    class Job:
        def result(self):
            return []

    class Client:
        project = "p"

        def __init__(self, *a, **k):
            pass

        def query(self, sql, **k):
            seen["sql"] = sql
            return Job()

    monkeypatch.setattr(bigquery, "Client", Client)
    from dbt_hakari.backends.bigquery import BigQueryBackend

    list(BigQueryBackend("p", "US").list_jobs(14))
    assert "`region-us`.INFORMATION_SCHEMA" in seen["sql"]
