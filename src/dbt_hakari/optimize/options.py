"""Options of one optimization run. Kept apart from application settings so that the optimizer
does not depend on how the CLI or a config file is organised."""

from __future__ import annotations

from pydantic import Field

from dbt_hakari.models import FrozenModel
from dbt_hakari.units import Count, PositiveFloat, Ratio


class OptimizeOptions(FrozenModel):
    max_k: Count = 8
    time_limit_s: PositiveFloat = 60.0
    mip_rel_gap: Ratio = 1e-6
    exclude: tuple[str, ...] = ()
    exclude_reasons: dict[str, str] = Field(default_factory=dict)  # selector -> why
    assume_daily: bool = False
