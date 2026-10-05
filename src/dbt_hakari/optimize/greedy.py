"""The obvious baseline: repeatedly materialize the single view that helps most."""

from __future__ import annotations

from collections.abc import Callable, Iterable


def greedy(
    candidates: Iterable[str],
    cost_of: Callable[[frozenset[str]], float],
    max_k: int,
) -> list[frozenset[str]]:
    """``result[k]`` is the greedy selection of size ``k`` (``result[0]`` is empty)."""
    remaining = sorted(candidates)
    chosen: frozenset[str] = frozenset()
    path = [chosen]
    for _ in range(min(max_k, len(remaining))):
        best = min(
            (v for v in remaining if v not in chosen),
            key=lambda v: (cost_of(chosen | {v}), v),
        )
        chosen = chosen | {best}
        path.append(chosen)
    return path
