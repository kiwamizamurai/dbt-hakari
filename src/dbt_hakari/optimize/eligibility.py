from __future__ import annotations

from collections.abc import Mapping

from dbt_hakari.costdata import CostData
from dbt_hakari.dbt.selectors import matching_pattern
from dbt_hakari.graph import FLAG_READS_INFORMATION_SCHEMA, Graph
from dbt_hakari.verify import VerificationReport


def select_candidates(
    graph: Graph,
    cost_data: CostData,
    exclude: list[str],
    reasons: Mapping[str, str] | None = None,
    verification: VerificationReport | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Views that may be materialized, and the reason every other view was left out."""
    candidates: list[str] = []
    excluded: dict[str, str] = {}
    for uid in graph.views():
        node = graph.nodes[uid]
        cost = cost_data.nodes.get(uid)
        if cost is None:
            excluded[uid] = "no compiled SQL / dry-run result"
        elif cost.error:
            excluded[uid] = f"dry-run failed: {cost.error}"
        elif FLAG_READS_INFORMATION_SCHEMA in node.flags:
            excluded[uid] = "reads INFORMATION_SCHEMA directly"
        elif verification is not None and uid in verification.excluded:
            excluded[uid] = f"failed verification: {verification.excluded[uid]}"
        elif (pattern := matching_pattern(node, exclude)) is not None:
            why = (reasons or {}).get(pattern)
            excluded[uid] = f"matches exclude pattern {pattern!r}" + (f" ({why})" if why else "")
        else:
            candidates.append(uid)
    return candidates, excluded
