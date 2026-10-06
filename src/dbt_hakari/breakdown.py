"""Where the bill goes: every daily query at today's setup, grouped and ranked."""

from __future__ import annotations

from typing import Literal

from dbt_hakari.cost.base import CostModel
from dbt_hakari.models import FrozenModel, Model
from dbt_hakari.optimize.problem import Problem, Query, bill

KINDS = ("tests", "builds", "models", "outside dbt")


class BreakdownRow(FrozenModel):
    label: str
    kind: str
    runs_per_day: float
    bytes_per_day: float
    share: float
    price_per_month: float


class Breakdown(Model):
    schema_version: Literal[1] = 1
    currency: str
    bytes_per_day: float
    price_per_month: float
    tib_per_month: float
    free_tib_per_month: float
    n_queries: int
    bytes_per_day_by_kind: dict[str, float]
    top: list[BreakdownRow]


def kind_of(query: Query) -> str:
    if query.key.startswith("external:"):
        return "outside dbt"
    if query.key.startswith("tests:"):
        return "tests"
    return "builds" if query.build_of is not None else "models"


def label_of(problem: Problem, query: Query) -> str:
    nodes = problem.graph.nodes
    kind = kind_of(query)
    if kind == "outside dbt":
        return nodes[query.key.removeprefix("external:")].name
    if kind == "tests":
        first, _, more = query.key.removeprefix("tests:").rpartition("+")
        extra = f" (+{more} identical)" if int(more) else ""
        return f"{nodes[first].name}{extra}"
    return nodes[query.build_of or query.key].name


def breakdown(problem: Problem, cost_model: CostModel, top: int = 15) -> Breakdown:
    """Bytes per day of every query that runs at today's setup (builds only of tables)."""
    today = problem.baseline
    rows: list[tuple[float, Query]] = []
    for query in problem.queries:
        if query.build_of is not None and query.build_of not in today:
            continue
        reads, floor = problem.walk(query.parents, today)
        rows.append((query.weight * bill(problem, query, reads, floor), query))
    total = sum(b for b, _ in rows)
    by_kind = {k: 0.0 for k in KINDS}
    for b, query in rows:
        by_kind[kind_of(query)] += b
    ranked = sorted(rows, key=lambda r: -r[0])[:top]
    month = 30 * total
    return Breakdown(
        currency=cost_model.currency,
        bytes_per_day=total,
        price_per_month=cost_model.price(month),
        tib_per_month=month / 1024**4,
        free_tib_per_month=cost_model.free_tib_per_month,
        n_queries=len(rows),
        bytes_per_day_by_kind=by_kind,
        top=[
            BreakdownRow(
                label=label_of(problem, q),
                kind=kind_of(q),
                runs_per_day=q.weight,
                bytes_per_day=b,
                share=b / total if total else 0.0,
                price_per_month=cost_model.price(30 * b),
            )
            for b, q in ranked
        ],
    )
