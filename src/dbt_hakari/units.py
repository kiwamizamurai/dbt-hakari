"""Constrained scalar types shared by the models that are read from or written to disk."""

from __future__ import annotations

from typing import Annotated

from annotated_types import Ge, Gt, Le

Bytes = Annotated[int, Ge(0)]
Count = Annotated[int, Ge(0)]
PositiveInt = Annotated[int, Gt(0)]
PositiveFloat = Annotated[float, Gt(0)]
Ratio = Annotated[float, Ge(0), Le(1)]
