"""Build the MILP.

Notation (see docs/formulation.md). For a query q and a node u that appears in q's SQL:

* ``x_v``      1 if view v is materialized as a table.
* ``r[q,u]``   how much u is read by q (0..1). Direct parents are read: r = 1.
* ``z[q,v]``   1 if q reads a materialized view v: it then counts as one table.
* ``c_q``      billed MiB of q, ``c_q >= max(10 MiB * tables(q), bytes(q), 10 MiB)``.

A view that is not materialized forwards the read to its parents:
``r[q,w] >= r[q,u] - x_u`` for each parent w of view u.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from dbt_hakari.cost.base import CostModel
from dbt_hakari.graph import Graph
from dbt_hakari.optimize.ir import MilpIR
from dbt_hakari.optimize.queries import Query

MIB = 1024**2


class Model:
    """The MILP together with the bookkeeping needed to read a solution back."""

    def __init__(self, ir: MilpIR, x: dict[str, int]) -> None:
        self.ir = ir
        self.x = x

    def selection(self, values: list[float]) -> frozenset[str]:
        return frozenset(v for v, col in self.x.items() if values[col] > 0.5)


def build_model(
    graph: Graph,
    queries: list[Query],
    candidates: list[str],
    leaf_weight: Mapping[str, int],
    cost_model: CostModel,
    *,
    max_views: int | None = None,
    force_table: Collection[str] = (),
    force_view: Collection[str] = (),
    tiebreak: float = 1e-3,
) -> Model:
    ir = MilpIR()
    unit = cost_model.min_unit_bytes() / MIB
    cand = frozenset(candidates)
    x = {v: ir.var(f"x[{v}]", 0, 1, integer=True, objective=tiebreak) for v in sorted(candidates)}
    for v in force_table:
        ir.lower[x[v]] = 1.0
    for v in force_view:
        ir.upper[x[v]] = 0.0

    for qi, q in enumerate(queries):
        is_build = q.build_of is not None
        reach = sorted(graph.reach_from(q.parents))
        r = {u: ir.var(f"r[{qi},{u}]", 0, 1) for u in reach}

        # direct parents are read (for a build: only if the view is actually materialized)
        for p in q.parents:
            if is_build:
                ir.row({r[p]: 1.0, x[q.build_of]: -1.0}, lo=0.0)  # type: ignore[index]
            else:
                ir.row({r[p]: 1.0}, lo=1.0, hi=1.0)

        # an expandable node that is not materialized forwards the read to its parents
        for u in reach:
            if not graph.is_expandable(u):
                continue
            for w in graph.nodes[u].parents:
                row = {r[w]: 1.0, r[u]: -1.0}
                if u in cand:
                    row[x[u]] = row.get(x[u], 0.0) + 1.0
                ir.row(row, lo=0.0)

        # tables read: leaves, plus materialized candidate views counted as one table each
        tables: dict[int, float] = {}
        for u in reach:
            if not graph.is_expandable(u):
                tables[r[u]] = tables.get(r[u], 0.0) + leaf_weight.get(u, 1)
            elif u in cand:
                z = ir.var(f"z[{qi},{u}]", 0, 1)
                ir.row({z: 1.0, r[u]: -1.0, x[u]: -1.0}, lo=-1.0)  # z >= r + x - 1
                tables[z] = 1.0

        bytes_mib = q.bytes_processed / MIB
        floor = max(bytes_mib, cost_model.billed_bytes(0, 0) / MIB)
        if is_build:
            c = ir.var(f"c[{qi}]", 0, objective=q.weight)
            ir.row({c: 1.0, x[q.build_of]: -floor}, lo=0.0)  # type: ignore[index]
        else:
            c = ir.var(f"c[{qi}]", floor, objective=q.weight)
        row = {c: 1.0}
        for col, weight in tables.items():
            row[col] = row.get(col, 0.0) - unit * weight
        ir.row(row, lo=0.0)

    if max_views is not None:
        ir.row({col: 1.0 for col in x.values()}, hi=float(max_views))
    return Model(ir, x)
