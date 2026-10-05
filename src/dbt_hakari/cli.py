"""Thin command line on top of the library. All logic lives in the library."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console

from dbt_hakari import __version__
from dbt_hakari.backends.base import Backend
from dbt_hakari.collect import collect as run_collect
from dbt_hakari.config import Settings, describe
from dbt_hakari.cost import BigQueryOnDemand
from dbt_hakari.cost.base import CostModel
from dbt_hakari.costdata import CostData
from dbt_hakari.dbt.manifest import load_manifest_with_warnings, manifest_sha256
from dbt_hakari.errors import GateFailedError, KannaError, UsageError
from dbt_hakari.graph import Graph
from dbt_hakari.history import History, build_history
from dbt_hakari.optimize import Plan
from dbt_hakari.optimize import optimize as run_optimize
from dbt_hakari.optimize.solvers.base import Solver
from dbt_hakari.optimize.solvers.scipy_highs import ScipyHighs
from dbt_hakari.report import (
    OutputFormat,
    plan_markdown,
    render_plan,
    render_verification,
    verification_markdown,
)
from dbt_hakari.store import DataStore
from dbt_hakari.suppressions import DEFAULT_FILE, load_suppressions
from dbt_hakari.verify import Verdict, VerificationReport, require_trust
from dbt_hakari.verify import verify as run_verify

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Verify BigQuery on-demand billing against your dbt project and find the optimal "
    "set of views to materialize. Read-only: dry-runs and INFORMATION_SCHEMA SELECTs only.",
)
console = Console()


@dataclass
class Dependencies:
    """The concrete pieces the CLI wires into the library. Tests replace them."""

    backend_factory: Callable[[Settings], Backend]
    solver: Solver


deps = Dependencies(
    backend_factory=lambda settings: _bigquery_backend(settings), solver=ScipyHighs()
)

EXIT_WARN_STRICT = 10

ManifestOpt = Annotated[Path, typer.Option("--manifest", help="dbt manifest.json")]
ConfigOpt = Annotated[Path | None, typer.Option("--config", help="hakari.toml")]
DataDirOpt = Annotated[Path | None, typer.Option("--data-dir", help="where results are kept")]
ProjectOpt = Annotated[str | None, typer.Option("--project", help="GCP project to bill/run in")]
LocationOpt = Annotated[str | None, typer.Option("--location", help="e.g. asia-northeast2, US")]
FormatOpt = Annotated[
    OutputFormat,
    typer.Option(
        "--format",
        help="text for people; json or markdown for CI (results on stdout, messages on stderr)",
    ),
]
PriceOpt = Annotated[float | None, typer.Option("--price-per-tib", help="on-demand price")]


def _bigquery_backend(settings: Settings) -> Backend:
    from dbt_hakari.backends.bigquery import BigQueryBackend

    if not settings.location:
        raise UsageError("--location is required (e.g. --location US or asia-northeast2)")
    return BigQueryBackend(
        settings.project, settings.location, max_bytes_billed=settings.history_max_bytes_billed
    )


def _settings(
    config: Path | None,
    data_dir: Path | None = None,
    project: str | None = None,
    location: str | None = None,
    price_per_tib: float | None = None,
    ignore_file: Path | None = None,
) -> Settings:
    settings = Settings.discover(config)
    settings.suppressions = load_suppressions(ignore_file or DEFAULT_FILE)
    if data_dir is not None:
        settings.data_dir = data_dir
    settings.project = project or settings.project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    settings.location = location or settings.location
    if price_per_tib is not None:
        settings.price_per_tib = price_per_tib
    return settings


def _cost_model(settings: Settings) -> BigQueryOnDemand:
    return BigQueryOnDemand(price_per_tib=settings.price_per_tib, currency=settings.currency)


def _fail(error: KannaError | ValidationError) -> None:
    if isinstance(error, ValidationError):  # a bad option value reached a validated model
        error = UsageError(describe(error))
    console.print(f"[red]error:[/red] {error}")
    raise typer.Exit(error.exit_code)


def _version(value: bool) -> None:
    _route_messages(OutputFormat.text)
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="show the version")
    ] = False,
) -> None:
    pass


@app.command()
def collect(
    manifest: ManifestOpt = Path("target/manifest.json"),
    config: ConfigOpt = None,
    data_dir: DataDirOpt = None,
    project: ProjectOpt = None,
    location: LocationOpt = None,
    threads: Annotated[int, typer.Option(min=1, help="parallel dry-runs")] = 8,
    lookback_days: Annotated[int, typer.Option(min=1, help="job history window")] = 14,
    no_history: Annotated[
        bool, typer.Option("--no-history", help="skip INFORMATION_SCHEMA")
    ] = False,
) -> None:
    """Dry-run every model and test, and read the recent job history.

    Dry-runs are free. The history read is one SELECT on INFORMATION_SCHEMA (about 0.5 GiB
    for 14 days), capped by maximum_bytes_billed. Use --no-history to skip it.
    """
    try:
        _route_messages(OutputFormat.text)
        settings = _settings(config, data_dir, project, location)
        settings.lookback_days = lookback_days
        graph, manifest_warnings = load_manifest_with_warnings(manifest)
        _warn(manifest_warnings)
        backend = deps.backend_factory(settings)
        store = DataStore(settings.data_dir)
        project_dir = manifest.resolve().parent.parent

        with console.status("dry-running every model and test ..."):
            cost_data = run_collect(
                graph,
                backend,
                project_dir=project_dir,
                threads=threads,
                project=settings.project,
                location=settings.location,
                manifest_sha256=manifest_sha256(manifest),
            )
        store.save_cost_data(cost_data)
        failed = [u for u, c in cost_data.nodes.items() if c.error]
        console.print(f"collected {len(cost_data.nodes)} nodes ({len(failed)} dry-run errors)")

        if not no_history:
            with console.status("reading job history ..."):
                history = build_history(backend.list_jobs(lookback_days), lookback_days)
            store.save_history(history)
            console.print(
                f"history: {history.n_jobs_total} jobs, {len(history.nodes)} attributed nodes, "
                f"{history.n_cache_hits} cache hits ignored"
            )
        console.print(f"saved to {settings.data_dir}/")
    except (KannaError, ValidationError) as error:
        _fail(error)


def _emit_verification(report: VerificationReport, fmt: OutputFormat) -> None:
    if fmt is OutputFormat.json:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=1))
    elif fmt is OutputFormat.markdown:
        typer.echo(verification_markdown(report))
    else:
        render_verification(console, report)


def _emit_plan(
    plan: Plan,
    report: VerificationReport,
    cost_model: CostModel,
    compare_greedy: bool,
    fmt: OutputFormat,
) -> None:
    if fmt is OutputFormat.json:
        payload = {
            "verification": report.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
        }
        typer.echo(json.dumps(payload, indent=1))
    elif fmt is OutputFormat.markdown:
        typer.echo(verification_markdown(report) + "\n\n" + plan_markdown(plan, cost_model))
    else:
        render_plan(console, plan, cost_model, compare_greedy)


def _warn(messages: list[str]) -> None:
    for message in messages:
        console.print(f"[yellow]warning:[/yellow] {message}")


def _route_messages(fmt: OutputFormat) -> None:
    """Machine-readable output owns stdout; messages for people go to stderr.

    Set on every command, because the console is shared by the whole process.
    """
    console.file = None if fmt is OutputFormat.text else sys.stderr  # type: ignore[assignment]


def _load_inputs(settings: Settings, manifest: Path) -> tuple[Graph, CostData, History | None]:
    store = DataStore(settings.data_dir)
    cost_data = store.load_cost_data()
    graph, manifest_warnings = load_manifest_with_warnings(manifest)
    _warn(manifest_warnings)
    if cost_data.manifest_sha256 and cost_data.manifest_sha256 != manifest_sha256(manifest):
        console.print("[yellow]warning:[/yellow] the manifest changed since `collect`; re-run it")
    return graph, cost_data, store.load_history()


@app.command()
def verify(
    manifest: ManifestOpt = Path("target/manifest.json"),
    config: ConfigOpt = None,
    data_dir: DataDirOpt = None,
    price_per_tib: PriceOpt = None,
    strict: Annotated[bool, typer.Option(help="exit 10 on WARN")] = False,
    output_format: FormatOpt = OutputFormat.text,
    recent_days: Annotated[
        int, typer.Option(min=1, help="compare with the bill of the last N days")
    ] = 3,
) -> None:
    """Check that the billing formula matches this project's real bill (the trust gate)."""
    try:
        _route_messages(output_format)
        settings = _settings(config, data_dir, price_per_tib=price_per_tib)
        graph, cost_data, history = _load_inputs(settings, manifest)
        report = run_verify(
            graph,
            cost_data,
            history,
            _cost_model(settings),
            thresholds=settings.gate_thresholds(),
            recent_days=recent_days,
        )
        DataStore(settings.data_dir).save_verification(report)
        _emit_verification(report, output_format)
        if report.verdict is Verdict.FAIL:
            raise typer.Exit(GateFailedError.exit_code)
        if strict and report.verdict is Verdict.WARN:
            raise typer.Exit(EXIT_WARN_STRICT)
    except (KannaError, ValidationError) as error:
        _fail(error)


@app.command()
def optimize(
    manifest: ManifestOpt = Path("target/manifest.json"),
    config: ConfigOpt = None,
    data_dir: DataDirOpt = None,
    price_per_tib: PriceOpt = None,
    max_k: Annotated[
        int | None, typer.Option(min=0, help="largest number of views to materialize")
    ] = None,
    exclude: Annotated[
        list[str] | None, typer.Option(help="tag:x, path:a/*, uid:..., or a name glob")
    ] = None,
    force_table: Annotated[
        list[str] | None, typer.Option(help="uid that must be materialized")
    ] = None,
    force_view: Annotated[list[str] | None, typer.Option(help="uid that must stay a view")] = None,
    assume_daily: Annotated[bool, typer.Option(help="count every query once a day")] = False,
    compare_greedy: Annotated[bool, typer.Option(help="also run the greedy baseline")] = False,
    time_limit: Annotated[float | None, typer.Option(min=0.001, help="seconds per solve")] = None,
    allow_unverified: Annotated[
        bool, typer.Option(help="run even if the trust gate fails")
    ] = False,
    recent_days: Annotated[int, typer.Option(min=1, help="verification window in days")] = 3,
    ignore_file: Annotated[
        Path | None, typer.Option(help="views that must stay views (default: .hakari-ignore)")
    ] = None,
    output_format: FormatOpt = OutputFormat.text,
) -> None:
    """Find the best views to materialize, for K = 0..max-k, and what each K saves."""
    try:
        _route_messages(output_format)
        settings = _settings(config, data_dir, price_per_tib=price_per_tib, ignore_file=ignore_file)
        if max_k is not None:
            settings.max_k = max_k
        if exclude:
            settings.exclude = [*settings.exclude, *exclude]
        if time_limit is not None:
            settings.time_limit_s = time_limit
        settings.assume_daily = assume_daily
        graph, cost_data, history = _load_inputs(settings, manifest)
        cost_model = _cost_model(settings)

        report = run_verify(
            graph,
            cost_data,
            history,
            cost_model,
            thresholds=settings.gate_thresholds(),
            recent_days=recent_days,
        )
        if output_format is OutputFormat.text:
            render_verification(console, report)
        try:
            unverified = require_trust(report, allow_unverified=allow_unverified)
        except GateFailedError:
            if output_format is not OutputFormat.text:
                _emit_verification(report, output_format)
            raise
        if unverified:
            console.print(
                "[red]WARNING: running on an unverified model; do not trust the numbers[/red]"
            )

        with console.status("solving ..."):
            plan = run_optimize(
                graph,
                cost_data,
                history,
                cost_model,
                settings.optimize_options(),
                solver=deps.solver,
                verification=report,
                compare_greedy=compare_greedy,
                force_table=tuple(force_table or ()),
                force_view=tuple(force_view or ()),
            )
        DataStore(settings.data_dir).save_plan(plan)
        _emit_plan(plan, report, cost_model, compare_greedy, output_format)
        if not plan.all_optimal:
            raise typer.Exit(6)
    except (KannaError, ValidationError) as error:
        _fail(error)
