"""Build the MILP for a :class:`Problem`.

For a query q and a node u that appears in q's SQL (``reach``):

* ``x_n``      1 if candidate n is a table.
* ``r[q,u]``   how much u is read by q (0..1). Direct parents are read: r = 1.
* ``z[q,n]``   1 if q reads candidate n as a table (``z >= r + x - 1``): it then counts as one
               table of size ``size(n)``.
* ``c_q``      billed MiB of q.

A node that is expanded (an ephemeral or ineligible view always, a candidate that is not a table)
forwards the read to its parents: ``r[q,w] >= r[q,u] - x_u``. All other costs only grow with
``r`` and ``z``, and the objective is minimized, so these one-sided lower bounds are enough: no
big-M, no upper bounds, no binary for the max.

The number of changes from today's setup is ``sum_{n not a table today} x_n +
sum_{n a table today} (1 - x_n)``.
"""

from __future__ import annotations

from collections.abc import Collection

from dbt_hakari.graph import EXPANDABLE_KINDS
from dbt_hakari.optimize.ir import MilpIR
from dbt_hakari.optimize.problem import Problem

MIB = 1024**2


class Model:
    """The MILP together with the bookkeeping needed to read a solution back."""

    def __init__(self, ir: MilpIR, x: dict[str, int]) -> None:
        self.ir = ir
        self.x = x

    def selection(self, values: list[float]) -> frozenset[str]:
        """The candidates that are tables in the solution."""
        return frozenset(v for v, col in self.x.items() if values[col] > 0.5)


def build_model(
    problem: Problem,
    *,
    max_changes: int | None = None,
    force_table: Collection[str] = (),
    force_view: Collection[str] = (),
    tiebreak: float = 1e-3,
) -> Model:
    graph = problem.graph
    cost = problem.cost_model
    ir = MilpIR()
    unit = cost.min_unit_bytes() / MIB
    floor = cost.billed_bytes(0, 0) / MIB
    candidates = problem.candidates
    baseline = problem.baseline

    x = {
        n: ir.var(f"x[{n}]", 0, 1, integer=True, objective=-tiebreak if n in baseline else tiebreak)
        for n in sorted(candidates)
    }
    for n in force_table:
        ir.lower[x[n]] = 1.0
    for n in force_view:
        ir.upper[x[n]] = 0.0

    def expandable(uid: str) -> bool:
        return uid in candidates or graph.nodes[uid].kind in EXPANDABLE_KINDS

    for qi, q in enumerate(problem.queries):
        build = q.build_of
        xb = x[build] if build is not None else None

        reach: set[str] = set()
        stack = list(q.parents)
        while stack:
            u = stack.pop()
            if u not in reach:
                reach.add(u)
                if expandable(u):
                    stack.extend(graph.nodes[u].parents)
        r = {u: ir.var(f"r[{qi},{u}]", 0, 1) for u in sorted(reach)}

        for p in q.parents:
            if xb is not None:
                ir.row({r[p]: 1.0, xb: -1.0}, lo=0.0)  # read only if the build happens
            else:
                ir.row({r[p]: 1.0}, lo=1.0, hi=1.0)

        for u in sorted(reach):
            if not expandable(u):
                continue
            for w in graph.nodes[u].parents:
                row = {r[w]: 1.0, r[u]: -1.0}
                if u in candidates:
                    row[x[u]] = row.get(x[u], 0.0) + 1.0
                ir.row(row, lo=0.0)

        # tables read: fixed leaves, and candidates that are tables
        terms: dict[int, tuple[float, int]] = {}  # column -> (size in bytes, tables behind it)
        for u in sorted(reach):
            if u in candidates:
                z = ir.var(f"z[{qi},{u}]", 0, 1)
                ir.row({z: 1.0, r[u]: -1.0, x[u]: -1.0}, lo=-1.0)  # z >= r + x - 1
                terms[z] = (candidates[u].size, 1)
            elif graph.nodes[u].kind not in EXPANDABLE_KINDS:
                terms[r[u]] = (problem.sizes.get(u, 0.0), problem.leaf_weight.get(u, 1))

        # a table today that is read as a view: it is computed again, at least ``floor`` bytes
        floors: dict[int, float] = {}
        for u in sorted(reach):
            if u in candidates and candidates[u].floor > 0:
                y = ir.var(f"y[{qi},{u}]", 0, 1)
                ir.row({y: 1.0, r[u]: -1.0, x[u]: 1.0}, lo=0.0)  # y >= r - x
                floors[y] = candidates[u].floor / MIB

        c = ir.var(f"c[{qi}]", 0, objective=q.weight)
        if xb is not None:
            ir.row({c: 1.0, xb: -floor}, lo=0.0)
        else:
            ir.lower[c] = floor

        # c >= minimum per table
        min_row = {c: 1.0}
        for col, (_, omega) in terms.items():
            min_row[col] = min_row.get(col, 0.0) - unit * omega
        ir.row(min_row, lo=0.0)

        if floors:
            row = {c: 1.0}
            for col, size in floors.items():
                row[col] = row.get(col, 0.0) - size
            ir.row(row, lo=0.0)

        if q.kappa is None:  # bytes are a constant
            bytes_mib = q.bytes_processed / MIB
            row = {c: 1.0}
            if xb is not None:
                row[xb] = -bytes_mib
            ir.row(row, lo=0.0 if xb is not None else bytes_mib)
        else:  # total bytes
            row = {c: 1.0}
            for col, (size, _) in terms.items():
                row[col] = row.get(col, 0.0) - q.kappa * size / MIB
            ir.row(row, lo=0.0)

    if max_changes is not None:
        row = {col: (-1.0 if n in baseline else 1.0) for n, col in x.items()}
        ir.row(row, hi=float(max_changes - len(baseline)))
    return Model(ir, x)
