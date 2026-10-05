from __future__ import annotations

from typing import Protocol


class CostModel(Protocol):
    """Maps (bytes processed, number of referenced tables) of one query to billed bytes."""

    @property
    def name(self) -> str: ...

    @property
    def currency(self) -> str: ...

    def price(self, billed_bytes: float) -> float:
        """Money for that many billed bytes."""
        ...

    def billed_bytes(self, processed: int, n_tables: int) -> int: ...

    def min_unit_bytes(self) -> int: ...

    def describe_assumptions(self) -> list[str]: ...
