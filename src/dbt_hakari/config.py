from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pydantic import ConfigDict, Field, ValidationError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from dbt_hakari.errors import UsageError
from dbt_hakari.models import Model
from dbt_hakari.optimize.options import Direction, OptimizeOptions
from dbt_hakari.units import Bytes, Count, PositiveFloat, PositiveInt, Ratio
from dbt_hakari.verify import GateThresholds


class Settings(Model):
    """Everything a config file (hakari.toml or [tool.dbt-hakari]) may set. Values are checked
    on load and on assignment, so a bad number is reported where it comes from."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    project: str | None = None
    location: str | None = None
    price_per_tib: PositiveFloat = 6.25
    currency: str = "USD"
    lookback_days: PositiveInt = 14
    threads: PositiveInt = 8
    history_max_bytes_billed: PositiveInt = 1024**3
    # verify gate
    tolerance: Ratio = 0.05
    min_match: Ratio = 0.90
    min_samples: Count = 20
    # optimize
    max_k: Count = 8
    time_limit_s: PositiveFloat = 60.0
    mip_rel_gap: Ratio = 1e-6
    exclude: list[str] = Field(default_factory=list)
    suppressions: dict[str, str] = Field(default_factory=dict)  # from .hakari-ignore
    assume_daily: bool = False
    direction: Direction = Direction.BOTH
    output_ratio: PositiveFloat = 1.0
    sizes: dict[str, Bytes] = Field(default_factory=dict)  # [sizes] uid or name -> bytes
    only_users: list[str] = Field(default_factory=list)  # count only jobs run by these users
    exclude_users: list[str] = Field(default_factory=list)  # ... and not these (a laptop)
    all_users: bool = False  # count everyone's dbt jobs, laptops included
    data_dir: Path = Path(".hakari")

    @classmethod
    def discover(cls, config: Path | None = None, directory: Path = Path()) -> Settings:
        """``--config`` if given, else ``hakari.toml``, else ``[tool.dbt-hakari]`` in pyproject."""
        if config is not None:
            if not config.exists():
                raise UsageError(f"config file not found: {config}")
            return cls.from_toml(config)
        for name in ("hakari.toml", "pyproject.toml"):
            candidate = directory / name
            if candidate.exists():
                settings = cls.from_toml(candidate)
                if name == "hakari.toml" or settings != cls():
                    return settings
        return cls()

    @classmethod
    def from_toml(cls, path: Path | None) -> Settings:
        if path is None or not path.exists():
            return cls()
        raw = tomllib.loads(path.read_text())
        if path.name == "pyproject.toml":
            raw = (raw.get("tool") or {}).get("dbt-hakari") or {}
        flat: dict[str, Any] = {}
        for name, section in raw.items():
            if name == "sizes":
                flat["sizes"] = section
            elif isinstance(section, dict):
                flat.update(section)
        try:
            return cls.model_validate(flat)
        except ValidationError as error:
            raise UsageError(f"{path}: {describe(error)}") from error

    def optimize_options(self) -> OptimizeOptions:
        return OptimizeOptions(
            max_k=self.max_k,
            time_limit_s=self.time_limit_s,
            mip_rel_gap=self.mip_rel_gap,
            exclude=(*self.exclude, *self.suppressions),
            exclude_reasons=dict(self.suppressions),
            assume_daily=self.assume_daily,
            direction=self.direction,
            output_ratio=self.output_ratio,
            sizes=dict(self.sizes),
        )

    def gate_thresholds(self) -> GateThresholds:
        return GateThresholds(
            tolerance=self.tolerance,
            graph_pass=self.min_match,
            bill_pass=self.min_match,
            min_samples=self.min_samples,
        )


def describe(error: ValidationError) -> str:
    """One readable line per problem: ``max_k: Input should be greater than or equal to 0``."""
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )
