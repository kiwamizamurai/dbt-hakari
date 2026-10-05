from __future__ import annotations

from dbt_hakari.graph import Graph, Node, NodeKind


def node(uid: str, kind: NodeKind, parents: tuple[str, ...] = ()) -> Node:
    return Node(uid=uid, name=uid, kind=kind, parents=parents)


def toy_graph(n_tests: int, n_tables: int = 3) -> Graph:
    """View ``V`` reads ``n_tables`` source tables; ``n_tests`` tests read ``V``."""
    sources = [f"s{i}" for i in range(n_tables)]
    nodes = [node(s, NodeKind.SOURCE) for s in sources]
    nodes.append(node("V", NodeKind.VIEW_MODEL, tuple(sources)))
    nodes += [node(f"t{i}", NodeKind.TEST, ("V",)) for i in range(n_tests)]
    return Graph({n.uid: n for n in nodes})


_MATERIALIZED = {
    NodeKind.VIEW_MODEL: "view",
    NodeKind.TABLE_MODEL: "table",
    NodeKind.INCREMENTAL_MODEL: "incremental",
    NodeKind.EPHEMERAL_MODEL: "ephemeral",
}


def graph_to_manifest(graph: Graph) -> dict:
    """The smallest manifest.json that load_manifest turns back into ``graph``."""
    nodes: dict = {}
    sources: dict = {}
    for uid, n in graph.nodes.items():
        raw = {
            "name": n.name,
            "relation_name": n.relation,
            "depends_on": {"nodes": list(n.parents)},
            "compiled_code": n.compiled_code,
            "original_file_path": n.path,
            "package_name": n.package,
            "tags": sorted(n.tags),
        }
        if n.kind is NodeKind.SOURCE:
            sources[uid] = raw
        elif n.kind is NodeKind.TEST:
            nodes[uid] = {**raw, "resource_type": "test", "config": {}}
        else:
            nodes[uid] = {
                **raw,
                "resource_type": "model",
                "config": {"materialized": _MATERIALIZED[n.kind]},
            }
    return {"metadata": {"dbt_version": "test"}, "nodes": nodes, "sources": sources}
