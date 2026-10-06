"""The dbt DAG, reduced to what the cost model needs.

Leaves are relations that physically exist (sources, seeds, snapshots, table and incremental
models, materialized views). Views and ephemeral models are *expanded*: a query that reads them
really reads whatever is beneath them. A view that is chosen for materialization becomes a leaf.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum


class NodeKind(str, Enum):
    SOURCE = "source"
    SEED = "seed"
    SNAPSHOT = "snapshot"
    TABLE_MODEL = "table_model"
    INCREMENTAL_MODEL = "incremental_model"
    VIEW_MODEL = "view_model"
    EPHEMERAL_MODEL = "ephemeral_model"
    MATERIALIZED_VIEW = "materialized_view"
    TEST = "test"
    OTHER = "other"


LEAF_KINDS = frozenset(
    {
        NodeKind.SOURCE,
        NodeKind.SEED,
        NodeKind.SNAPSHOT,
        NodeKind.TABLE_MODEL,
        NodeKind.INCREMENTAL_MODEL,
        NodeKind.MATERIALIZED_VIEW,
    }
)
EXPANDABLE_KINDS = frozenset({NodeKind.VIEW_MODEL, NodeKind.EPHEMERAL_MODEL})
EXECUTING_MODEL_KINDS = frozenset({NodeKind.TABLE_MODEL, NodeKind.INCREMENTAL_MODEL})

FLAG_READS_INFORMATION_SCHEMA = "reads_information_schema"


@dataclass(frozen=True)
class Node:
    uid: str
    name: str
    kind: NodeKind
    relation: str | None = None
    parents: tuple[str, ...] = ()
    tags: frozenset[str] = frozenset()
    path: str | None = None
    package: str | None = None
    compiled_code: str | None = None
    compiled_path: str | None = None
    flags: frozenset[str] = frozenset()


@dataclass
class Graph:
    nodes: dict[str, Node]
    exposed: frozenset[str] = frozenset()  # read by something dbt cannot see (exposures)
    _order: list[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        for node in self.nodes.values():
            missing = [p for p in node.parents if p not in self.nodes]
            if missing:
                raise ValueError(f"{node.uid} has unknown parents: {missing}")
        self._order = sorted(self.nodes)

    def relation_index(self) -> dict[str, str]:
        """Normalized relation (``project.dataset.name``) -> uid, for everything that exists. When
        two nodes share a relation, a model wins over a source."""
        index: dict[str, str] = {}
        for uid, n in sorted(self.nodes.items(), key=lambda item: item[1].kind is NodeKind.SOURCE):
            if n.relation and n.kind is not NodeKind.TEST:
                index.setdefault(relation_key(n.relation), uid)
        return index

    def duplicate_relations(self) -> dict[str, list[str]]:
        """Relations that more than one node claims (a source and the model that rebuilds it)."""
        seen: dict[str, list[str]] = {}
        for uid, n in self.nodes.items():
            if n.relation and n.kind is not NodeKind.TEST:
                seen.setdefault(relation_key(n.relation), []).append(uid)
        return {k: v for k, v in seen.items() if len(v) > 1}

    # -- classification -------------------------------------------------------------------

    def is_expandable(self, uid: str) -> bool:
        return self.nodes[uid].kind in EXPANDABLE_KINDS

    def is_view(self, uid: str) -> bool:
        return self.nodes[uid].kind is NodeKind.VIEW_MODEL

    def views(self) -> list[str]:
        return [u for u in self._order if self.is_view(u)]

    def executing_models(self) -> list[str]:
        return [u for u in self._order if self.nodes[u].kind in EXECUTING_MODEL_KINDS]

    def tests(self) -> list[str]:
        return [u for u in self._order if self.nodes[u].kind is NodeKind.TEST]

    def leaves(self) -> list[str]:
        return [u for u in self._order if self.nodes[u].kind in LEAF_KINDS]

    # -- expansion ------------------------------------------------------------------------

    def reach_from(
        self, parents: Iterable[str], materialized: frozenset[str] = frozenset()
    ) -> set[str]:
        """Every node that appears in a query reading ``parents`` (views expanded, stopping at
        leaves and at materialized views)."""
        seen: set[str] = set()
        stack = list(parents)
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            if self.is_expandable(node) and node not in materialized:
                stack.extend(self.nodes[node].parents)
        return seen

    def reach(self, uid: str, materialized: frozenset[str] = frozenset()) -> set[str]:
        return self.reach_from(self.nodes[uid].parents, materialized)

    def leaves_from(
        self, parents: Iterable[str], materialized: frozenset[str] = frozenset()
    ) -> set[str]:
        """Distinct physical relations read by a query that reads ``parents``."""
        return {
            n
            for n in self.reach_from(parents, materialized)
            if not self.is_expandable(n) or n in materialized
        }

    def leaves_of(self, uid: str, materialized: frozenset[str] = frozenset()) -> set[str]:
        return self.leaves_from(self.nodes[uid].parents, materialized)

    def table_count_from(
        self,
        parents: Iterable[str],
        leaf_weight: Mapping[str, int] | None = None,
        materialized: frozenset[str] = frozenset(),
    ) -> int:
        weights = leaf_weight or {}
        return sum(weights.get(n, 1) for n in self.leaves_from(parents, materialized))

    def table_count(
        self,
        uid: str,
        leaf_weight: Mapping[str, int] | None = None,
        materialized: frozenset[str] = frozenset(),
    ) -> int:
        return self.table_count_from(self.nodes[uid].parents, leaf_weight, materialized)


def relation_key(relation: str) -> str:
    """Normalize a relation to ``project.dataset.table`` (dbt prints it with backticks and BigQuery
    ids may differ in case)."""
    return relation.replace("`", "").lower()


def graph_from_nodes(nodes: Iterable[Node]) -> Graph:
    return Graph({n.uid: n for n in nodes})
