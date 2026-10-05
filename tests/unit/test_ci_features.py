"""JSON/markdown output, manifest checks, .hakari-ignore and pyproject.toml settings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from dbt_hakari import cli
from dbt_hakari.config import Settings
from dbt_hakari.dbt.manifest import load_manifest_with_warnings
from dbt_hakari.errors import ManifestError, UsageError
from dbt_hakari.store import DataStore
from dbt_hakari.suppressions import load_suppressions
from tests.helpers import graph_to_manifest
from tests.world import build_world

runner = CliRunner()


def prepare(tmp_path, monkeypatch, **world):
    graph, backend = build_world(**world)
    manifest = tmp_path / "target" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps(graph_to_manifest(graph)))
    monkeypatch.setattr(cli.deps, "backend_factory", lambda settings: backend)
    monkeypatch.chdir(tmp_path)
    args = ["--manifest", str(manifest), "--data-dir", str(tmp_path / ".hakari")]
    assert runner.invoke(cli.app, ["collect", *args, "--location", "US"]).exit_code == 0
    return args


def test_verify_json_goes_to_stdout_alone(tmp_path, monkeypatch):
    args = prepare(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["verify", *args, "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["verdict"] == "PASS"


def test_optimize_json_has_verification_and_plan(tmp_path, monkeypatch):
    args = prepare(tmp_path, monkeypatch)
    result = runner.invoke(
        cli.app, ["optimize", *args, "--assume-daily", "--max-k", "1", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verification"]["verdict"] == "PASS"
    assert payload["plan"]["recommended_k"] == 1


def test_markdown_output(tmp_path, monkeypatch):
    args = prepare(tmp_path, monkeypatch)
    result = runner.invoke(
        cli.app, ["optimize", *args, "--assume-daily", "--max-k", "1", "--format", "markdown"]
    )
    assert result.exit_code == 0
    assert "### dbt-hakari trust gate: PASS" in result.stdout
    assert "| K | GiB/day |" in result.stdout


def test_failed_gate_still_prints_json_and_exits_4(tmp_path, monkeypatch):
    args = prepare(tmp_path, monkeypatch, billed_per_table=40 * 1024**2)
    result = runner.invoke(cli.app, ["optimize", *args, "--assume-daily", "--format", "json"])
    assert result.exit_code == 4
    assert json.loads(result.stdout)["verdict"] == "FAIL"


def write_manifest(path: Path, **overrides) -> Path:
    graph, _ = build_world(n_tests=2)
    manifest = graph_to_manifest(graph)
    manifest.update(overrides)
    path.write_text(json.dumps(manifest))
    return path


def test_old_schema_is_refused(tmp_path):
    path = write_manifest(
        tmp_path / "m.json",
        metadata={"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v7.json"},
    )
    with pytest.raises(ManifestError, match="v9 or newer"):
        load_manifest_with_warnings(path)


def test_recent_schema_is_accepted(tmp_path):
    path = write_manifest(
        tmp_path / "m.json",
        metadata={"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"},
    )
    assert load_manifest_with_warnings(path)[1] == []


def test_missing_schema_version_warns(tmp_path):
    _, warnings = load_manifest_with_warnings(write_manifest(tmp_path / "m.json"))
    assert any("dbt_schema_version" in w for w in warnings)


def test_a_manifest_without_compiled_sql_is_refused(tmp_path):
    path = tmp_path / "m.json"
    manifest = json.loads(write_manifest(path).read_text())
    for node in manifest["nodes"].values():
        node["compiled_code"] = None
    path.write_text(json.dumps(manifest))
    with pytest.raises(ManifestError, match="dbt compile"):
        load_manifest_with_warnings(path)


def test_partly_compiled_manifest_warns(tmp_path):
    path = tmp_path / "m.json"
    manifest = json.loads(write_manifest(path).read_text())
    manifest["nodes"]["t0"]["compiled_code"] = None
    path.write_text(json.dumps(manifest))
    _, warnings = load_manifest_with_warnings(path)
    assert any("1 of" in w and "no compiled SQL" in w for w in warnings)


def test_suppressions_file_parses_selectors_and_reasons(tmp_path):
    path = tmp_path / ".hakari-ignore"
    path.write_text("# header\n\nV   # read by BI\ntag:keep\npath:models/adhoc/*  # scratch\n")
    assert load_suppressions(path) == {
        "V": "read by BI",
        "tag:keep": "",
        "path:models/adhoc/*": "scratch",
    }
    assert load_suppressions(tmp_path / "missing") == {}


def test_suppressed_view_is_not_materialized_and_the_reason_is_shown(tmp_path, monkeypatch):
    args = prepare(tmp_path, monkeypatch)
    (tmp_path / ".hakari-ignore").write_text("V  # read by an external tool\n")
    result = runner.invoke(
        cli.app, ["optimize", *args, "--assume-daily", "--max-k", "1", "--format", "json"]
    )
    plan = json.loads(result.stdout)["plan"]
    assert plan["curve"][-1]["chosen"] == []
    assert plan["n_candidates"] == 0
    assert "read by an external tool" in plan["excluded"]["V"]


def test_settings_are_read_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.dbt-hakari.bigquery]\nlocation = "US"\n[tool.dbt-hakari.optimize]\nmax_k = 3\n'
    )
    settings = Settings.discover(directory=tmp_path)
    assert (settings.location, settings.max_k) == ("US", 3)


def test_hakari_toml_wins_over_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.dbt-hakari.optimize]\nmax_k = 3\n")
    (tmp_path / "hakari.toml").write_text("[optimize]\nmax_k = 5\n")
    assert Settings.discover(directory=tmp_path).max_k == 5


def test_pyproject_without_our_table_is_ignored(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    assert Settings.discover(directory=tmp_path) == Settings()


def test_wrongly_typed_setting_is_reported(tmp_path):
    (tmp_path / "hakari.toml").write_text(
        '[optimize]\nmax_k = "three"\n'.replace("kanna", "hakari")
    )
    with pytest.raises(UsageError, match="max_k"):
        Settings.discover(directory=tmp_path)


def test_out_of_range_setting_is_reported_with_its_name(tmp_path):
    (tmp_path / "hakari.toml").write_text("[verify]\ntolerance = 2\n[optimize]\nmax_k = -1\n")
    with pytest.raises(UsageError) as error:
        Settings.discover(directory=tmp_path)
    assert "tolerance" in str(error.value) and "max_k" in str(error.value)


def test_unknown_setting_is_reported(tmp_path):
    (tmp_path / "hakari.toml").write_text("[optimize]\nmax_kk = 3\n")
    with pytest.raises(UsageError, match="max_kk"):
        Settings.discover(directory=tmp_path)


def test_assignment_is_validated():
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.max_k = -1


def test_settings_reach_the_trust_gate():
    thresholds = Settings(tolerance=0.01, min_match=0.99, min_samples=7).gate_thresholds()
    assert (thresholds.tolerance, thresholds.graph_pass, thresholds.min_samples) == (0.01, 0.99, 7)


def test_cli_rejects_a_negative_max_k(tmp_path, monkeypatch):
    args = prepare(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["optimize", *args, "--max-k", "-1"])
    assert result.exit_code == 2


def test_a_corrupt_data_file_asks_for_a_new_collect(tmp_path, monkeypatch):
    args = prepare(tmp_path, monkeypatch)
    costdata = tmp_path / ".hakari" / "costdata.json"
    payload = json.loads(costdata.read_text())
    payload["schema_version"] = 99
    costdata.write_text(json.dumps(payload))
    result = runner.invoke(cli.app, ["verify", *args])
    assert result.exit_code == 2
    assert "re-run" in result.output and "collect" in result.output


def test_saved_files_round_trip(tmp_path, monkeypatch):
    args = prepare(tmp_path, monkeypatch)
    runner.invoke(cli.app, ["optimize", *args, "--assume-daily", "--max-k", "1"])
    store = DataStore(tmp_path / ".hakari")
    cost_data = store.load_cost_data()
    store.save_cost_data(cost_data)
    assert store.load_cost_data() == cost_data
