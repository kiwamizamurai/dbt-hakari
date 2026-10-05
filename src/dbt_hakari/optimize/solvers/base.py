from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from dbt_hakari.optimize.ir import MilpIR


class SolveStatus(str, Enum):
    OPTIMAL = "optimal"
    TIME_LIMIT = "time_limit"
    INFEASIBLE = "infeasible"
    ERROR = "error"


@dataclass(frozen=True)
class SolveResult:
    status: SolveStatus
    values: list[float] | None
    objective: float | None
    gap: float | None = None


class Solver(Protocol):
    def solve(self, ir: MilpIR, time_limit: float, mip_rel_gap: float) -> SolveResult: ...
