"""The obvious baseline: repeatedly materialize the single view that helps most."""

from __future__ import annotations

from collections.abc import Callable, Iterable


def greedy(
    candidates: Iterable[str],
    cost_of: Callable[[frozenset[str]], float],
    max_k: int,
) -> list[frozenset[str]]:
    """``result[k]`` is what greedy selection reaches with *at most* ``k`` views.

    Like the exact solution (which allows at most K views), greedy stops adding views once the
    best addition no longer lowers the cost. Forcing it to add harmful views would exaggerate the
    gap to the optimum. ``result[0]`` is empty.
    """
    remaining = sorted(candidates)
    chosen: frozenset[str] = frozenset()
    path = [chosen]
    for _ in range(min(max_k, len(remaining))):
        options = [v for v in remaining if v not in chosen]
        if options:
            best = min(options, key=lambda v: (cost_of(chosen | {v}), v))
            if cost_of(chosen | {best}) < cost_of(chosen):
                chosen = chosen | {best}
        path.append(chosen)
    return path
