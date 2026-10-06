"""Terminal rendering of verification reports and plans. No logic beyond formatting."""

from __future__ import annotations

from enum import Enum

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from dbt_hakari.breakdown import Breakdown
from dbt_hakari.cost.base import CostModel
from dbt_hakari.explain import Explanation
from dbt_hakari.optimize import Change, Plan
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
        console.print(f"  [yellow]note:[/yellow] {escape(note)}")


def _change_words(change: Change) -> str:
    return "make a table" if change is Change.TO_TABLE else "make a view"


def summary_lines(plan: Plan) -> list[str]:
    """What the plan means in money, and whether it is worth the trouble."""
    now, after = plan.price_per_month_now, plan.price_per_month_after
    saved = now - after
    lines = [
        f"today: {_gib(plan.baseline_bytes_per_day)} GiB/day = {now:,.2f} {plan.currency}/month "
        f"({plan.tib_per_month_now:.2f} TiB; the first {plan.free_tib_per_month:g} TiB/month of "
        "the whole project is free)"
    ]
    if plan.external_bytes_per_day > 0:
        lines.append(
            f"  of which {_gib(plan.external_bytes_per_day)} GiB/day are queries from outside dbt "
            "(apps and BI services): changing a model changes what they read, so they are counted"
        )
    if not plan.recommendations:
        lines.append("recommended: no change")
        return lines
    top = plan.recommendations[0]
    point = plan.curve[plan.recommended_k]
    total_bytes = plan.baseline_bytes_per_day - point.objective_bytes_per_day
    lines.append(
        f"recommended: {_gib(point.objective_bytes_per_day)} GiB/day "
        f"(-{point.saving_pct:.0f}%, saves {saved:,.2f} {plan.currency}/month)"
    )
    # the changes of a pair that only pays off together each "save" the whole gain when removed
    # alone, so the shares add up to more than the total: they say nothing about "one change"
    shares = (
        [r.saving_bytes_per_day / total_bytes for r in plan.recommendations]
        if total_bytes > 0
        else []
    )
    if len(shares) > 1 and sum(shares) <= 1.05 and shares[0] >= 0.6:
        lines.append(
            f"{shares[0]:.0%} of the saving comes from one change: {top.name}. "
            "The others are worth little"
        )
    if saved < 1.0:
        lines.append(
            f"small in money terms ({saved:,.2f} {plan.currency}/month): worth doing only if it "
            "is cheap to do, and nothing if the whole project stays inside the free tier"
        )
    return lines


def render_plan(console: Console, plan: Plan, cost_model: CostModel, compare_greedy: bool) -> None:
    console.print(
        f"\n{plan.n_candidates} models may change, {plan.n_queries} distinct daily queries"
        + (" (every query counted once a day)" if plan.assume_daily else "")
    )
    table = Table(
        "changes",
        "GiB/day",
        "saving",
        "per month",
        *(["greedy GiB/day"] if compare_greedy else []),
    )
    base = plan.baseline_bytes_per_day
    for p in plan.curve:
        monthly = cost_model.price((base - p.objective_bytes_per_day) * 30)
        row = [
            f"<= {p.k}",
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
            f"({u.saving_pct:.0f}% saving) with {len(u.chosen)} changes"
        )
    console.print()
    for line in summary_lines(plan):
        console.print(line)
    if not plan.recommendations:
        console.print("Nothing here is worth changing.")
    else:
        for r in plan.recommendations:
            saving = _gib(r.saving_bytes_per_day)
            console.print(f"  {_change_words(r.change)}: {r.name}  saves {saving} GiB/day")
    for note in plan.notes:
        console.print(f"[yellow]note:[/yellow] {escape(note)}")
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
        f"{plan.n_candidates} models may change, {plan.n_queries} distinct daily queries.",
        "",
        "| changes | GiB/day | saving | per month |",
        "|---|---|---|---|",
    ]
    for p in plan.curve:
        monthly = cost_model.price((base - p.objective_bytes_per_day) * 30)
        lines.append(
            f"| <= {p.k} | {_gib(p.objective_bytes_per_day)} | {p.saving_pct:.0f}% | "
            f"{monthly:,.2f} {cost_model.currency} |"
        )
    lines += ["", *[f"- {line}" for line in summary_lines(plan)], ""]
    if plan.recommendations:
        lines += [f"**Recommended: {plan.recommended_k} change(s)**", ""]
        lines += [
            f"- {_change_words(r.change)}: `{r.name}` saves {_gib(r.saving_bytes_per_day)} GiB/day"
            for r in plan.recommendations
        ]
    else:
        lines += ["", "**Recommended: no change.** Nothing here is worth changing."]
    lines += [f"\n> {note}" for note in plan.notes]
    return "\n".join(lines)


def _money_line(b: Breakdown) -> str:
    return (
        f"today: {_gib(b.bytes_per_day)} GiB/day = {b.price_per_month:,.2f} {b.currency}/month "
        f"({b.tib_per_month:.2f} TiB; the first {b.free_tib_per_month:g} TiB/month of the whole "
        "project is free)"
    )


def render_breakdown(console: Console, b: Breakdown) -> None:
    console.print(f"{b.n_queries} daily queries at today's setup")
    console.print(escape(_money_line(b)))
    kinds = Table("kind", "GiB/day", "share", box=None, pad_edge=False)
    for kind, value in sorted(b.bytes_per_day_by_kind.items(), key=lambda kv: -kv[1]):
        if value:
            kinds.add_row(kind, _gib(value), f"{value / b.bytes_per_day:.0%}")
    console.print(kinds)
    table = Table("#", "what", "kind", "runs/day", "GiB/day", "share", "per month")
    for i, row in enumerate(b.top, 1):
        table.add_row(
            str(i),
            row.label,
            row.kind,
            f"{row.runs_per_day:.2g}",
            _gib(row.bytes_per_day),
            f"{row.share:.0%}",
            f"{row.price_per_month:,.2f} {b.currency}",
        )
    console.print(table)


def breakdown_markdown(b: Breakdown) -> str:
    lines = [
        "### dbt-hakari report",
        "",
        _money_line(b),
        "",
        "| kind | GiB/day | share |",
        "|---|---|---|",
    ]
    for kind, value in sorted(b.bytes_per_day_by_kind.items(), key=lambda kv: -kv[1]):
        if value:
            lines.append(f"| {kind} | {_gib(value)} | {value / b.bytes_per_day:.0%} |")
    lines += [
        "",
        "| # | what | kind | runs/day | GiB/day | share | per month |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, row in enumerate(b.top, 1):
        lines.append(
            f"| {i} | `{row.label}` | {row.kind} | {row.runs_per_day:.2g} | "
            f"{_gib(row.bytes_per_day)} | {row.share:.0%} | "
            f"{row.price_per_month:,.2f} {b.currency} |"
        )
    return "\n".join(lines)


def _explanation_head(e: Explanation) -> list[str]:
    lines = [f"{e.name} ({e.kind}, {e.uid})"]
    if e.build_bytes is not None:
        lines.append(f"its SQL scans {e.build_bytes / 1024**3:.2f} GiB")
    if not e.candidate:
        lines.append(f"left as it is: {e.why_not}")
        return lines
    size = f"{_gib(e.table_size_bytes or 0)} GiB" + (
        " (estimated)" if e.table_size_estimated else ""
    )
    lines.append(f"as a table it is {size}")
    verb = "make a table" if e.change is Change.TO_TABLE else "make a view"
    now, after, saving = (
        e.bytes_per_day_now or 0.0,
        e.bytes_per_day_after or 0.0,
        e.saving_bytes_per_day or 0.0,
    )
    lines.append(f"{verb}: {_gib(now)} -> {_gib(after)} GiB/day across everything that reads it")
    if saving > 0:
        money = f"{e.price_per_month_saving or 0:,.2f} {e.currency}/month"
        lines.append(f"saves {_gib(saving)} GiB/day = {money}")
    else:
        lines.append(f"would cost {_gib(-saving)} GiB/day more: keep it as it is")
    if e.in_last_plan is not None:
        lines.append(
            "part of the last `optimize` recommendation"
            if e.in_last_plan
            else "not part of the last `optimize` recommendation"
        )
    return lines


def render_explanation(console: Console, e: Explanation) -> None:
    for line in _explanation_head(e):
        console.print(escape(line))
    if e.readers:
        table = Table("reads it", "kind", "runs/day", "GiB/day now", "GiB/day after")
        for r in e.readers:
            table.add_row(
                r.label,
                r.kind,
                f"{r.runs_per_day:.2g}",
                _gib(r.bytes_per_day_now),
                "-" if r.bytes_per_day_after is None else _gib(r.bytes_per_day_after),
            )
        console.print(table)
    for note in e.notes:
        console.print(f"[yellow]note:[/yellow] {escape(note)}")


def explanation_markdown(e: Explanation) -> str:
    lines = ["### dbt-hakari explain", "", *[f"- {line}" for line in _explanation_head(e)]]
    if e.readers:
        lines += [
            "",
            "| reads it | kind | runs/day | GiB/day now | GiB/day after |",
            "|---|---|---|---|---|",
        ]
        for r in e.readers:
            after = "-" if r.bytes_per_day_after is None else _gib(r.bytes_per_day_after)
            lines.append(
                f"| `{r.label}` | {r.kind} | {r.runs_per_day:.2g} | "
                f"{_gib(r.bytes_per_day_now)} | {after} |"
            )
    lines += [f"\n> {note}" for note in e.notes]
    return "\n".join(lines)
