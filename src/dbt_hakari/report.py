"""Terminal rendering of verification reports and plans. No logic beyond formatting."""

from __future__ import annotations

from enum import Enum

from rich.console import Console
from rich.table import Table

from dbt_hakari.cost.base import CostModel
from dbt_hakari.optimize import Plan
from dbt_hakari.verify import Verdict, VerificationReport


def _gib(value: float) -> str:
    return f"{value / 1024**3:,.2f}"


def render_verification(console: Console, report: VerificationReport) -> None:
    colour = {Verdict.PASS: "green", Verdict.WARN: "yellow", Verdict.FAIL: "red"}[report.verdict]
    console.print(f"trust gate: [{colour}]{report.verdict.value}[/{colour}]")
    table = Table(show_header=False, box=None)
    table.add_row(
        "graph == dry-run table counts",
        f"{report.graph_match_rate:.1%} of {report.n_graph_checked} nodes",
    )
    if report.bill_within_tol_rate is not None:
        t = report.thresholds.tolerance
        table.add_row(
            f"bill within {t:.0%} of the formula",
            f"{report.bill_within_tol_rate:.1%} of {report.n_bill_samples} nodes",
        )
        table.add_row("median |error|", f"{report.median_abs_err:.2%}")
        table.add_row(
            "formula too high / too low",
            f"{report.overestimate_rate:.1%} / {report.underestimate_rate:.1%}",
        )
    else:
        table.add_row("billed samples", "none")
    table.add_row("nodes left out", str(len(report.excluded)))
    console.print(table)
    for note in report.notes:
        console.print(f"  [yellow]note:[/yellow] {note}")


def render_plan(console: Console, plan: Plan, cost_model: CostModel, compare_greedy: bool) -> None:
    console.print(
        f"\n{plan.n_candidates} candidate views, {plan.n_queries} distinct daily queries"
        + (" (every query counted once a day)" if plan.assume_daily else "")
    )
    table = Table(
        "K", "GiB/day", "saving", "per month", *(["greedy GiB/day"] if compare_greedy else [])
    )
    base = plan.baseline_bytes_per_day
    for p in plan.curve:
        monthly = cost_model.price((base - p.objective_bytes_per_day) * 30)
        row = [
            str(p.k),
            _gib(p.objective_bytes_per_day),
            f"{p.saving_pct:.0f}%",
            f"{monthly:,.2f} {cost_model.currency}",
        ]
        if compare_greedy:
            g = p.greedy_objective_bytes_per_day
            row.append("-" if g is None else f"{_gib(g)} (+{_gib(g - p.objective_bytes_per_day)})")
        table.add_row(*row)
    console.print(table)
    if plan.unconstrained:
        u = plan.unconstrained
        console.print(
            f"unconstrained optimum: {_gib(u.objective_bytes_per_day)} GiB/day "
            f"({u.saving_pct:.0f}% saving) with {len(u.chosen)} views"
        )
    console.print(f"\nrecommended: materialize {plan.recommended_k} view(s)")
    for r in plan.recommendations:
        console.print(f"  {r.name}  saves {_gib(r.saving_bytes_per_day)} GiB/day")
    for note in plan.notes:
        console.print(f"[yellow]note:[/yellow] {note}")
    console.print("[dim]estimates: " + "; ".join(plan.assumptions[:2]) + "[/dim]")


class OutputFormat(str, Enum):
    text = "text"
    json = "json"
    markdown = "markdown"


def verification_markdown(report: VerificationReport) -> str:
    rows = [
        (
            "graph == dry-run table counts",
            f"{report.graph_match_rate:.1%} of {report.n_graph_checked} nodes",
        ),
    ]
    if report.bill_within_tol_rate is not None:
        rows += [
            (
                f"bill within {report.thresholds.tolerance:.0%} of the formula",
                f"{report.bill_within_tol_rate:.1%} of {report.n_bill_samples} nodes",
            ),
            ("median abs error", f"{report.median_abs_err:.2%}"),
        ]
    rows.append(("nodes left out", str(len(report.excluded))))
    lines = [
        f"### dbt-hakari trust gate: {report.verdict.value}",
        "",
        "| check | result |",
        "|---|---|",
    ]
    lines += [f"| {name} | {value} |" for name, value in rows]
    lines += [f"\n> {note}" for note in report.notes]
    return "\n".join(lines)


def plan_markdown(plan: Plan, cost_model: CostModel) -> str:
    base = plan.baseline_bytes_per_day
    lines = [
        "### dbt-hakari plan",
        "",
        f"{plan.n_candidates} candidate views, {plan.n_queries} distinct daily queries.",
        "",
        "| K | GiB/day | saving | per month |",
        "|---|---|---|---|",
    ]
    for p in plan.curve:
        monthly = cost_model.price((base - p.objective_bytes_per_day) * 30)
        lines.append(
            f"| {p.k} | {_gib(p.objective_bytes_per_day)} | {p.saving_pct:.0f}% | "
            f"{monthly:,.2f} {cost_model.currency} |"
        )
    lines += ["", f"**Recommended: materialize {plan.recommended_k} view(s)**", ""]
    lines += [
        f"- `{r.name}`: saves {_gib(r.saving_bytes_per_day)} GiB/day" for r in plan.recommendations
    ]
    lines += [f"\n> {note}" for note in plan.notes]
    return "\n".join(lines)
