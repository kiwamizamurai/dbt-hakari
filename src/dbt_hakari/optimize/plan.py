from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field

from dbt_hakari.models import FrozenModel, Model
from dbt_hakari.optimize.solvers.base import SolveStatus
from dbt_hakari.units import Count
from dbt_hakari.verify import Verdict


class Change(str, Enum):
    TO_TABLE = "table"  # a view that should become a table
    TO_VIEW = "view"  # a table that should become a view


class KPoint(Model):
    k: Count  # at most this many changes
    objective_bytes_per_day: float
    saving_pct: float
    chosen: tuple[str, ...]  # the models whose materialization changes
    to_table: tuple[str, ...] = ()
    to_view: tuple[str, ...] = ()
    solver_status: SolveStatus = SolveStatus.OPTIMAL
    mip_gap: float | None = None
    greedy_objective_bytes_per_day: float | None = None
    greedy_chosen: tuple[str, ...] | None = None


class Recommendation(FrozenModel):
    uid: str
    name: str
    change: Change
    saving_bytes_per_day: float  # marginal: cost(without this change) - cost(with it)


class Plan(Model):
    schema_version: Literal[1] = 1
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
    currency: str = "USD"
    price_per_month_now: float = 0.0  # before the monthly free tier
    price_per_month_after: float = 0.0  # with the recommended changes
    tib_per_month_now: float = 0.0
    external_bytes_per_day: float = 0.0  # of the baseline: queries from outside dbt
    free_tib_per_month: float = 1.0
    unconstrained: KPoint | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def all_optimal(self) -> bool:
        points = self.curve + ([self.unconstrained] if self.unconstrained else [])
        return all(p.solver_status is SolveStatus.OPTIMAL for p in points)
