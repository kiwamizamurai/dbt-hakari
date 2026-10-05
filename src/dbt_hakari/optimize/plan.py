from __future__ import annotations

from pydantic import Field

from dbt_hakari.models import FrozenModel, Model
from dbt_hakari.optimize.solvers.base import SolveStatus
from dbt_hakari.units import Count
from dbt_hakari.verify import Verdict


class KPoint(Model):
    k: Count
    objective_bytes_per_day: float
    saving_pct: float
    chosen: tuple[str, ...]
    solver_status: SolveStatus = SolveStatus.OPTIMAL
    mip_gap: float | None = None
    greedy_objective_bytes_per_day: float | None = None
    greedy_chosen: tuple[str, ...] | None = None


class Recommendation(FrozenModel):
    uid: str
    name: str
    saving_bytes_per_day: float  # marginal: cost(S without v) - cost(S)


class Plan(Model):
    baseline_bytes_per_day: float
    curve: list[KPoint]
    recommended_k: Count
    recommendations: list[Recommendation]
    excluded: dict[str, str]
    assumptions: list[str]
    assume_daily: bool
    verification_verdict: Verdict | None
    n_candidates: Count
    n_queries: Count
    unconstrained: KPoint | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def all_optimal(self) -> bool:
        points = self.curve + ([self.unconstrained] if self.unconstrained else [])
        return all(p.solver_status is SolveStatus.OPTIMAL for p in points)
