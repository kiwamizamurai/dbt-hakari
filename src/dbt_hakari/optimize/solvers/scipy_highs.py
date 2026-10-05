"""HiGHS through ``scipy.optimize.milp``: ships wheels for every platform, no external binary."""

from __future__ import annotations

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from dbt_hakari.optimize.ir import MilpIR
from dbt_hakari.optimize.solvers.base import SolveResult, SolveStatus


class ScipyHighs:
    def solve(self, ir: MilpIR, time_limit: float, mip_rel_gap: float) -> SolveResult:
        matrix = coo_matrix(
            (ir.values, (ir.row_index, ir.col_index)), shape=(ir.n_rows, ir.n_vars)
        ).tocsr()
        result = milp(
            c=np.array(ir.objective),
            constraints=LinearConstraint(matrix, np.array(ir.row_lo), np.array(ir.row_hi)),
            integrality=np.array([1 if i else 0 for i in ir.integer]),
            bounds=Bounds(np.array(ir.lower), np.array(ir.upper)),
            options={"time_limit": time_limit, "mip_rel_gap": mip_rel_gap, "presolve": True},
        )
        if result.x is None:
            failure = {2: SolveStatus.INFEASIBLE, 1: SolveStatus.TIME_LIMIT}.get(
                result.status, SolveStatus.ERROR
            )
            return SolveResult(failure, None, None)
        status = SolveStatus.OPTIMAL if result.status == 0 else SolveStatus.TIME_LIMIT
        gap = getattr(result, "mip_gap", None)
        return SolveResult(status, [float(v) for v in result.x], float(result.fun), gap)
