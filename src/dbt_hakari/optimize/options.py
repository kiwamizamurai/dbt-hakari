"""Options of one optimization run. Kept apart from application settings so that the optimizer
does not depend on how the CLI or a config file is organised."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from dbt_hakari.models import FrozenModel
from dbt_hakari.units import Bytes, Count, PositiveFloat, Ratio


class Direction(str, Enum):
    """Which changes may be recommended."""

    VIEWS = "views"  # only views that become tables
    TABLES = "tables"  # only tables that become views
    BOTH = "both"


class OptimizeOptions(FrozenModel):
    max_k: Count = 8  # at most this many changes
    time_limit_s: PositiveFloat = 60.0
    mip_rel_gap: Ratio = 1e-6
    exclude: tuple[str, ...] = ()
    exclude_reasons: dict[str, str] = Field(default_factory=dict)  # selector -> why
    assume_daily: bool = False
    direction: Direction = Direction.BOTH
    # bytes of a view's table once materialized, relative to the bytes its query processes. 1.0
    # assumes no reduction (the cautious choice); give per-view sizes below when you know better
    output_ratio: PositiveFloat = 1.0
    sizes: dict[str, Bytes] = Field(default_factory=dict)  # uid or name -> bytes of its table
