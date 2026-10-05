"""Read a dbt ``manifest.json`` (dbt-core 1.x or dbt 2.0) into a :class:`Graph`.

The manifest is read as plain JSON, so dbt-hakari does not depend on dbt itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from dbt_hakari.errors import ManifestError
from dbt_hakari.graph import FLAG_READS_INFORMATION_SCHEMA, Graph, Node, NodeKind

_MATERIALIZATION_KINDS = {
    "view": NodeKind.VIEW_MODEL,
    "table": NodeKind.TABLE_MODEL,
    "incremental": NodeKind.INCREMENTAL_MODEL,
    "ephemeral": NodeKind.EPHEMERAL_MODEL,
    "materialized_view": NodeKind.MATERIALIZED_VIEW,
}


def manifest_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _kind_of(raw: dict[str, Any]) -> NodeKind:
    resource_type = raw.get("resource_type")
    if resource_type == "model":
        materialized = (raw.get("config") or {}).get("materialized")
        return _MATERIALIZATION_KINDS.get(materialized or "", NodeKind.OTHER)
    if resource_type == "seed":
        return NodeKind.SEED
    if resource_type == "snapshot":
        return NodeKind.SNAPSHOT
    if resource_type == "test":
        return NodeKind.TEST
    return NodeKind.OTHER


def _flags(code: str | None) -> frozenset[str]:
    if code and "information_schema" in code.lower():
        return frozenset({FLAG_READS_INFORMATION_SCHEMA})
    return frozenset()


MIN_SCHEMA_VERSION = 9  # dbt-core 1.5; earlier manifests lack fields dbt-hakari relies on
_SCHEMA_VERSION = re.compile(r"/manifest/v(\d+)\.json")


def _check_schema_version(manifest: dict[str, Any], path: Path, warnings: list[str]) -> None:
    url = (manifest.get("metadata") or {}).get("dbt_schema_version")
    match = _SCHEMA_VERSION.search(url or "")
    if match is None:
        warnings.append("the manifest has no dbt_schema_version; assuming a recent dbt")
    elif int(match.group(1)) < MIN_SCHEMA_VERSION:
        raise ManifestError(
            f"{path} uses manifest schema v{match.group(1)}; dbt-hakari needs "
            f"v{MIN_SCHEMA_VERSION} or newer (dbt-core 1.5+). Re-run `dbt compile` with a newer dbt"
        )


def _check_compiled(graph: Graph, path: Path, warnings: list[str]) -> None:
    executing = [*graph.executing_models(), *graph.tests()]
    missing = [
        uid
        for uid in executing
        if not graph.nodes[uid].compiled_code and not graph.nodes[uid].compiled_path
    ]
    if executing and len(missing) == len(executing):
        raise ManifestError(
            f"{path} has no compiled SQL: run `dbt compile` first (`dbt parse` is not enough)"
        )
    if missing:
        warnings.append(
            f"{len(missing)} of {len(executing)} models and tests have no compiled SQL and will "
            "be skipped; run `dbt compile` for the whole project"
        )


def load_manifest(path: Path) -> Graph:
    return load_manifest_with_warnings(path)[0]


def load_manifest_with_warnings(path: Path) -> tuple[Graph, list[str]]:
    """The graph, plus things the user should know about (never a reason to stop)."""
    warnings: list[str] = []
    try:
        manifest = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read manifest {path}: {error}") from error
    if "nodes" not in manifest:
        raise ManifestError(f"{path} does not look like a dbt manifest (no 'nodes')")

    _check_schema_version(manifest, path, warnings)
    raw_nodes: dict[str, dict[str, Any]] = {**manifest["nodes"], **manifest.get("sources", {})}
    nodes: dict[str, Node] = {}
    for uid, raw in raw_nodes.items():
        is_source = uid in manifest.get("sources", {})
        code = raw.get("compiled_code")
        nodes[uid] = Node(
            uid=uid,
            name=raw.get("name", uid.rsplit(".", 1)[-1]),
            kind=NodeKind.SOURCE if is_source else _kind_of(raw),
            relation=raw.get("relation_name"),
            parents=tuple(
                p for p in (raw.get("depends_on") or {}).get("nodes", []) if p in raw_nodes
            ),
            tags=frozenset(raw.get("tags") or ()),
            path=raw.get("original_file_path") or raw.get("path"),
            package=raw.get("package_name"),
            compiled_code=code,
            compiled_path=raw.get("compiled_path"),
            flags=_flags(code),
        )
    graph = Graph(nodes)
    _check_compiled(graph, path, warnings)
    return graph, warnings
