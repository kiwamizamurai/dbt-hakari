"""Run frequencies must be able to ignore laptops: only the scheduled load is worth optimizing."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from dbt_hakari import cli
from dbt_hakari.backends.base import JobRecord
from dbt_hakari.graph import relation_key
from dbt_hakari.history import UNKNOWN_USER, build_history
from tests.helpers import graph_to_manifest
from tests.world import build_world

MIB = 1024**2
SCHEDULER = "sa-scheduler@proj.iam.gserviceaccount.com"
LAPTOP = "Dev.Person@example.com"


def jobs():
    runs = [JobRecord("model.x.m", 10 * MIB, user=SCHEDULER, creation_day="2026-01-01")] * 14
    runs += [JobRecord("model.x.m", 10 * MIB, user=LAPTOP, creation_day="2026-01-02")] * 28
    runs += [JobRecord("model.x.only_laptop", 10 * MIB, user=LAPTOP)] * 5
    runs += [JobRecord("model.x.nobody", 10 * MIB)]  # no user recorded
    ext = [
        JobRecord(
            None, 9, from_dbt=False, processed_bytes=100 * MIB, user="app", referenced=("p.d.t",)
        )
    ] * 10
    ext += [
        JobRecord(
            None, 9, from_dbt=False, processed_bytes=20 * MIB, user=LAPTOP, referenced=("p.d.t",)
        )
    ] * 30
    return runs + ext


@pytest.fixture
def history():
    return build_history(jobs(), 14, {"p.d.t": "model.x.t"})


def test_jobs_are_counted_per_user(history):
    assert history.nodes["model.x.m"].jobs_by_user == {SCHEDULER: 14, LAPTOP: 28}
    assert history.nodes["model.x.nobody"].jobs_by_user == {UNKNOWN_USER: 1}
    assert history.users[LAPTOP] == 28 + 5 + 30
    assert next(iter(history.users)) == LAPTOP  # most active first


def test_a_laptop_can_be_left_out_and_the_frequency_follows(history):
    assert history.nodes["model.x.m"].runs_per_day == pytest.approx(42 / 14)
    only_schedule = history.select_users(exclude=["*@example.com"])
    assert only_schedule.nodes["model.x.m"].runs_per_day == pytest.approx(1.0)
    assert "model.x.only_laptop" not in only_schedule.nodes
    assert only_schedule.external["model.x.t"].runs_per_day == pytest.approx(10 / 14)
    assert only_schedule.external["model.x.t"].bytes_processed == 100 * MIB


def test_only_picks_users_and_matching_ignores_case(history):
    mine = history.select_users(only=["dev.person@EXAMPLE.com"])
    assert mine.nodes["model.x.m"].runs_per_day == pytest.approx(28 / 14)
    external = mine.external["model.x.t"]
    assert external.runs_per_day == pytest.approx(30 / 14)
    assert external.bytes_processed == 20 * MIB


def test_the_size_of_other_peoples_queries_is_weighted_by_how_often_each_user_runs(history):
    both = history.select_users()
    expected = (10 * 100 + 30 * 20) // 40
    assert both.external["model.x.t"].bytes_processed == expected * MIB


def test_a_history_saved_before_users_were_counted_is_kept_whole(history):
    old = history.model_copy(deep=True)
    old.nodes["model.x.m"] = old.nodes["model.x.m"].model_copy(update={"jobs_by_user": {}})
    kept = old.select_users(exclude=["*"])
    assert "model.x.m" in kept.nodes
    assert kept.nodes["model.x.m"].runs_per_day == old.nodes["model.x.m"].runs_per_day


def test_users_command_lists_who_ran_what(tmp_path, monkeypatch):
    graph, backend = build_world(user="sa@x.test")
    manifest = tmp_path / "target" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps(graph_to_manifest(graph)))
    monkeypatch.setattr(cli.deps, "backend_factory", lambda settings: backend)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    args = ["--manifest", str(manifest), "--data-dir", str(tmp_path / ".hakari")]
    assert runner.invoke(cli.app, ["collect", *args, "--location", "US"]).exit_code == 0
    result = runner.invoke(cli.app, ["users", "--data-dir", str(tmp_path / ".hakari")])
    assert result.exit_code == 0, result.output
    assert "sa@x.test" in result.output
    assert "--exclude-user" in result.output


def test_relation_keys_are_normalized():
    assert relation_key("`P`.`D`.`T`") == "p.d.t"


def mixed_world(tmp_path, monkeypatch):
    """Tests ran 1x a day from a service account and 3x a day from a laptop."""
    from dataclasses import replace

    from dbt_hakari.backends import FakeBackend

    graph, backend = build_world(n_tests=25, runs_per_test=4)
    jobs = []
    for i, job in enumerate(backend._jobs):
        jobs.append(replace(job, user=SERVICE if i % 4 == 0 else "dev@example.com"))
    backend = FakeBackend(backend._dry_runs, jobs)
    manifest = tmp_path / "target" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps(graph_to_manifest(graph)))
    monkeypatch.setattr(cli.deps, "backend_factory", lambda settings: backend)
    monkeypatch.chdir(tmp_path)
    args = ["--manifest", str(manifest), "--data-dir", str(tmp_path / ".hakari")]
    assert CliRunner().invoke(cli.app, ["collect", *args, "--location", "US"]).exit_code == 0
    return args


SERVICE = "sa@p.iam.gserviceaccount.com"


def baseline(args, *extra):
    result = CliRunner().invoke(cli.app, ["optimize", *args, "--format", "json", *extra])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["plan"]["baseline_bytes_per_day"], result.output


def test_laptop_runs_are_ignored_by_default_and_the_user_is_told(tmp_path, monkeypatch):
    args = mixed_world(tmp_path, monkeypatch)
    scheduled, output = baseline(args)
    everyone, _ = baseline(args, "--all-users")
    explicit, _ = baseline(args, "--only-user", SERVICE)
    assert scheduled == pytest.approx(explicit)
    assert everyone > scheduled * 1.5  # the laptop runs inflate the load
    assert "counting only what service accounts ran" in output


def test_all_users_counts_everyone_without_a_note(tmp_path, monkeypatch):
    args = mixed_world(tmp_path, monkeypatch)
    _, output = baseline(args, "--all-users")
    assert "service accounts" not in output


def test_an_explicit_choice_replaces_the_automatic_one(tmp_path, monkeypatch):
    args = mixed_world(tmp_path, monkeypatch)
    _, output = baseline(args, "--exclude-user", "dev@*")
    assert "history restricted by user" in output
    assert "counting only what service accounts ran" not in output


def test_with_nobody_to_separate_nothing_changes():
    from dbt_hakari.history import build_history

    only_people = build_history(
        [JobRecord("model.x.m", 10 * MIB, user="dev@example.com")] * 3, 14, {}
    )
    history, note = only_people.scheduled()
    assert note is None and history is only_people


def test_people_s_ad_hoc_queries_are_not_part_of_the_scheduled_load():
    app = "bi@p.iam.gserviceaccount.com"
    runs = [JobRecord("model.x.m", 10 * MIB, user=SCHEDULER)] * 14
    runs += [JobRecord("model.x.m", 10 * MIB, user=LAPTOP)] * 28
    runs += [
        JobRecord(
            None, 9, from_dbt=False, processed_bytes=50 * MIB, user=app, referenced=("p.d.a",)
        )
    ] * 10
    runs += [
        JobRecord(
            None, 9, from_dbt=False, processed_bytes=50 * MIB, user=LAPTOP, referenced=("p.d.b",)
        )
    ] * 30
    history = build_history(runs, 14, {"p.d.a": "model.x.a", "p.d.b": "model.x.b"})
    scheduled, note = history.scheduled()
    assert note is not None and "service accounts" in note
    assert scheduled.nodes["model.x.m"].runs_per_day == pytest.approx(1.0)  # not 3.0
    assert set(scheduled.external) == {"model.x.a"}  # the BI service's queries stay
    assert set(history.external) == {"model.x.a", "model.x.b"}
