"""A solver-independent mixed-integer linear program in sparse form."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MilpIR:
    names: list[str] = field(default_factory=list)
    objective: list[float] = field(default_factory=list)
    lower: list[float] = field(default_factory=list)
    upper: list[float] = field(default_factory=list)
    integer: list[bool] = field(default_factory=list)
    # constraints: lo <= sum(coeff * x) <= hi
    row_index: list[int] = field(default_factory=list)
    col_index: list[int] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    row_lo: list[float] = field(default_factory=list)
    row_hi: list[float] = field(default_factory=list)

    def var(
        self,
        name: str,
        lower: float = 0.0,
        upper: float = float("inf"),
        integer: bool = False,
        objective: float = 0.0,
    ) -> int:
        self.names.append(name)
        self.objective.append(objective)
        self.lower.append(lower)
        self.upper.append(upper)
        self.integer.append(integer)
        return len(self.names) - 1

    def row(
        self, coeffs: dict[int, float], lo: float = float("-inf"), hi: float = float("inf")
    ) -> None:
        index = len(self.row_lo)
        for col in sorted(coeffs):
            self.row_index.append(index)
            self.col_index.append(col)
            self.values.append(coeffs[col])
        self.row_lo.append(lo)
        self.row_hi.append(hi)

    @property
    def n_vars(self) -> int:
        return len(self.names)

    @property
    def n_rows(self) -> int:
        return len(self.row_lo)
