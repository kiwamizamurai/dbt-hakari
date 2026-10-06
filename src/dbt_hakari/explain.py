"""Why a model is, or is not, worth changing: who reads it and what each reader pays."""

from __future__ import annotations

from typing import Literal

from dbt_hakari.cost.base import CostModel
from dbt_hakari.costdata import CostData
from dbt_hakari.errors import UsageError
from dbt_hakari.graph import Graph, NodeKind
from dbt_hakari.models import FrozenModel, Model
from dbt_hakari.optimize.plan import Change, Plan
from dbt_hakari.optimize.problem import Query, bill
from dbt_hakari.optimize.sweep import Prepared

TOP_READERS = 12


class ReaderRow(FrozenModel):
    label: str
    kind: str  # tests, builds, models, outside dbt
    runs_per_day: float
    bytes_per_day_now: float
    bytes_per_day_after: float | None  # when the model changes; None if it cannot change


class Explanation(Model):
    schema_version: Literal[1] = 1
    uid: str
    name: str
    kind: str
    candidate: bool
    why_not: str | None = None
    table_size_bytes: float | None = None  # known, or estimated for a view that does not exist
    table_size_estimated: bool = False
    build_bytes: float | None = None  # what its SQL scans
    change: Change | None = None  # what changing it would be
    bytes_per_day_now: float | None = None
    bytes_per_day_after: float | None = None
    saving_bytes_per_day: float | None = None
    price_per_month_saving: float | None = None
    currency: str = "USD"
    in_last_plan: bool | None = None  # None: no plan was saved
    readers: list[ReaderRow]
    notes: list[str]


def resolve(graph: Graph, name: str) -> str:
    """A uid, or a name that identifies one node."""
    if name in graph.nodes:
        return name
    named = sorted(
        u for u, n in graph.nodes.items() if n.name == name and n.kind is not NodeKind.TEST
    )
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        raise UsageError(f"{name!r} is ambiguous: use the uid, one of {', '.join(named[:4])}")
    close = sorted(n.name for n in graph.nodes.values() if name.lower() in n.name.lower())[:5]
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise UsageError(f"no model named {name!r}.{hint}")


def explain(
    prepared: Prepared,
    graph: Graph,
    cost_data: CostData,
    cost_model: CostModel,
    name: str,
    plan: Plan | None = None,
) -> Explanation:
    from dbt_hakari.breakdown import kind_of, label_of

    problem = prepared.problem
    uid = resolve(graph, name)
    node = graph.nodes[uid]
    today = problem.baseline
    candidate = problem.candidates.get(uid)
    flipped = problem.tables_from_flips({uid})
    after_tables = flipped if candidate else None

    def cost(query: Query, tables: frozenset[str]) -> float:
        if query.build_of is not None and query.build_of not in tables:
            return 0.0
        reads, floor = problem.walk(query.parents, tables)
        return query.weight * bill(problem, query, reads, floor)

    readers = []
    for query in problem.queries:
        if not problem.reaches(query.parents, uid) and query.build_of != uid:
            continue
        now = cost(query, today)
        after = cost(query, after_tables) if after_tables is not None else None
        if now or after:
            readers.append(
                ReaderRow(
                    label=label_of(problem, query),
                    kind=kind_of(query),
                    runs_per_day=query.weight,
                    bytes_per_day_now=now,
                    bytes_per_day_after=after,
                )
            )
    readers.sort(key=lambda r: -max(r.bytes_per_day_now, r.bytes_per_day_after or 0.0))

    explanation = Explanation(
        uid=uid,
        name=node.name,
        kind={NodeKind.VIEW_MODEL: "view", NodeKind.TABLE_MODEL: "table"}.get(
            node.kind, node.kind.value
        ),
        candidate=candidate is not None,
        currency=cost_model.currency,
        build_bytes=cost_data.nodes[uid].bytes_processed if uid in cost_data.nodes else None,
        readers=readers[:TOP_READERS],
        notes=[],
    )
    if candidate is None:
        explanation.why_not = prepared.excluded.get(uid) or (
            "it is not a view or a table model (sources, seeds, snapshots, incremental and "
            "ephemeral models are left as they are)"
        )
        return explanation

    now = sum(cost(q, today) for q in problem.queries)
    after = sum(cost(q, flipped) for q in problem.queries)
    explanation.change = Change.TO_VIEW if uid in today else Change.TO_TABLE
    explanation.bytes_per_day_now = now
    explanation.bytes_per_day_after = after
    explanation.saving_bytes_per_day = now - after
    explanation.price_per_month_saving = cost_model.price(30 * (now - after))
    explanation.table_size_bytes = candidate.size
    explanation.table_size_estimated = uid not in cost_data.leaf_bytes
    if explanation.table_size_estimated:
        explanation.notes.append(
            "the size of its table is estimated from the bytes its SQL scans "
            "(--output-ratio): no reduction is credited unless you set one"
        )
    if explanation.change is Change.TO_VIEW:
        explanation.notes.append(
            "as a view, every read is assumed to recompute what its SQL scans: a cautious "
            "estimate, checked on an experiment but not on your project"
        )
    if plan is not None:
        explanation.in_last_plan = uid in plan.curve[plan.recommended_k].chosen
    return explanation
