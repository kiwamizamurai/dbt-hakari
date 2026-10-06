"""Which models may change, and why every other model may not."""

from __future__ import annotations

from dbt_hakari.costdata import CostData, NodeCost
from dbt_hakari.dbt.selectors import matching_pattern, validate
from dbt_hakari.graph import FLAG_READS_INFORMATION_SCHEMA, Graph, NodeKind
from dbt_hakari.history import History
from dbt_hakari.optimize.options import Direction, OptimizeOptions
from dbt_hakari.optimize.problem import Candidate
from dbt_hakari.verify import VerificationReport


def select_candidates(
    graph: Graph,
    cost_data: CostData,
    history: History | None,
    options: OptimizeOptions,
    verification: VerificationReport | None = None,
) -> tuple[dict[str, Candidate], dict[str, str]]:
    """Models that may be a view or a table, and the reason every other view or table model
    was left as it is."""
    candidates: dict[str, Candidate] = {}
    excluded: dict[str, str] = {}
    exclude = list(options.exclude)
    validate(exclude)
    for uid, node in graph.nodes.items():
        if node.kind is NodeKind.VIEW_MODEL:
            if options.direction is Direction.TABLES:
                continue
        elif node.kind is NodeKind.TABLE_MODEL:
            if options.direction is Direction.VIEWS:
                continue
        else:
            continue
        is_table = node.kind is NodeKind.TABLE_MODEL
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
            why = options.exclude_reasons.get(pattern)
            excluded[uid] = f"matches exclude pattern {pattern!r}" + (f" ({why})" if why else "")
        elif is_table and uid not in cost_data.leaf_bytes:
            excluded[uid] = "size of the table is unknown (collect it again)"
        elif is_table and uid in graph.exposed:
            excluded[uid] = "read by an exposure, which dbt cannot measure"
        elif is_table and (history is None or not history.external_known):
            excluded[uid] = "who else reads this table is unknown (collect with history)"
        else:
            size = size_of(uid, node.name, cost, cost_data, options)
            floor = cost.bytes_processed if is_table else 0.0
            candidates[uid] = Candidate(uid, is_table, size, floor)
    return candidates, excluded


def size_of(
    uid: str, name: str, cost: NodeCost, cost_data: CostData, options: OptimizeOptions
) -> float:
    """Bytes the node's table has, or would have: known if it is a table today, otherwise the
    given size or ``output_ratio`` times the bytes its query processes."""
    if uid in options.sizes:
        return float(options.sizes[uid])
    if name in options.sizes:
        return float(options.sizes[name])
    if uid in cost_data.leaf_bytes:
        return float(cost_data.leaf_bytes[uid])
    return options.output_ratio * cost.bytes_processed
