from __future__ import annotations

import json

from typer.testing import CliRunner

from dbt_hakari import cli
from dbt_hakari.dbt.manifest import load_manifest
from tests.helpers import graph_to_manifest
from tests.world import MIB, build_world

runner = CliRunner()


def make_project(tmp_path, monkeypatch, **world):
    graph, backend = build_world(**world)
    manifest = tmp_path / "target" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps(graph_to_manifest(graph)))
    monkeypatch.setattr(cli.deps, "backend_factory", lambda settings: backend)
    data = tmp_path / ".hakari"
    common = ["--manifest", str(manifest), "--data-dir", str(data)]
    return common, data


def invoke(*args):
    result = runner.invoke(cli.app, [*args])
    return result


def test_manifest_round_trip(tmp_path):
    graph, _ = build_world(n_tests=3)
    path = tmp_path / "m.json"
    path.write_text(json.dumps(graph_to_manifest(graph)))
    loaded = load_manifest(path)
    assert set(loaded.nodes) == set(graph.nodes)
    assert loaded.table_count("t0") == 3


def test_version():
    assert invoke("--version").exit_code == 0


def test_collect_verify_optimize_end_to_end(tmp_path, monkeypatch):
    common, data = make_project(tmp_path, monkeypatch)
    assert invoke("collect", *common, "--location", "US").exit_code == 0
    assert (data / "costdata.json").exists() and (data / "history.json").exists()

    result = invoke("verify", *common)
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output

    result = invoke("optimize", *common, "--max-k", "2", "--assume-daily", "--compare-greedy")
    assert result.exit_code == 0, result.output
    plan = json.loads((data / "plan.json").read_text())
    assert plan["recommended_k"] == 1
    assert plan["curve"][1]["chosen"] == ["V"]
    assert plan["verification_verdict"] == "PASS"


def test_a_failing_gate_refuses_to_optimize_unless_forced(tmp_path, monkeypatch):
    common, data = make_project(tmp_path, monkeypatch, billed_per_table=5 * MIB)
    assert invoke("collect", *common, "--location", "US").exit_code == 0
    assert invoke("verify", *common).exit_code == 4
    refused = invoke("optimize", *common, "--assume-daily")
    assert refused.exit_code == 4
    assert not (data / "plan.json").exists()
    forced = invoke("optimize", *common, "--assume-daily", "--allow-unverified")
    assert forced.exit_code == 0
    assert "unverified" in forced.output


def test_optimize_without_collect_is_a_usage_error(tmp_path, monkeypatch):
    common, _ = make_project(tmp_path, monkeypatch)
    result = invoke("optimize", *common)
    assert result.exit_code == 2
    assert "collect" in result.output


def test_collect_needs_a_location(tmp_path, monkeypatch):
    make_project(tmp_path, monkeypatch)
    monkeypatch.undo()  # use the real backend factory, which demands a location
    result = invoke("collect", "--manifest", str(tmp_path / "target" / "manifest.json"))
    assert result.exit_code == 2
    assert "--location" in result.output
