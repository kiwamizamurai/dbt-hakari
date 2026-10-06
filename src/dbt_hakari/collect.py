"""Dry-run every dbt model and test (and every leaf relation) to learn bytes and table counts."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from dbt_hakari.backends.base import Backend
from dbt_hakari.costdata import CostData, NodeCost
from dbt_hakari.errors import BackendError
from dbt_hakari.graph import Graph, Node, NodeKind


def node_sql(node: Node, project_dir: Path | None) -> str | None:
    """The compiled SQL of a node: from the manifest if present, else from the compiled file."""
    if node.compiled_code:
        return node.compiled_code
    if node.compiled_path and project_dir is not None:
        path = project_dir / node.compiled_path
        if path.exists():
            return path.read_text()
    return None


def ensure_some_dry_run_worked(cost_data: CostData) -> None:
    """When every dry-run fails the setup is wrong (region, project, credentials), not a model."""
    errors = [c.error for c in cost_data.nodes.values()]
    if errors and all(errors):
        raise BackendError(
            f"every dry-run failed (the first error: {errors[0]}). Check --location (the region "
            "of your datasets), --project, and your credentials "
            "(`gcloud auth application-default login`)"
        )


def collect(
    graph: Graph,
    backend: Backend,
    *,
    project_dir: Path | None = None,
    threads: int = 8,
    project: str | None = None,
    location: str | None = None,
    manifest_sha256: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> CostData:
    sql_nodes: dict[str, str] = {}
    for uid, node in graph.nodes.items():
        if node.kind in (NodeKind.SOURCE, NodeKind.SEED, NodeKind.OTHER, NodeKind.EPHEMERAL_MODEL):
            continue
        sql = node_sql(node, project_dir)
        if sql:
            sql_nodes[uid] = sql
    leaf_relations = {
        uid: graph.nodes[uid].relation for uid in graph.leaves() if graph.nodes[uid].relation
    }

    total = len(sql_nodes) + len(leaf_relations)
    done = 0

    def tick() -> None:
        nonlocal done
        done += 1
        if on_progress:
            on_progress(done, total)

    def measure(item: tuple[str, str]) -> NodeCost:
        uid, sql = item
        try:
            result = backend.dry_run(sql)
            return NodeCost(
                uid=uid,
                bytes_processed=result.bytes_processed,
                n_tables=len(result.referenced_tables),
                referenced=tuple(sorted(result.referenced_tables)),
            )
        except BackendError as error:
            return NodeCost(uid=uid, bytes_processed=0, n_tables=0, error=str(error)[:300])
        finally:
            tick()

    def leaf_weight(item: tuple[str, str]) -> tuple[str, tuple[int, int] | None]:
        uid, relation = item
        try:
            result = backend.dry_run(f"SELECT * FROM {relation}")
            return uid, (max(1, len(result.referenced_tables)), result.bytes_processed)
        except BackendError:
            return uid, None
        finally:
            tick()

    with ThreadPoolExecutor(max(1, threads)) as pool:
        costs = list(pool.map(measure, sorted(sql_nodes.items())))
        weights = dict(pool.map(leaf_weight, sorted(leaf_relations.items())))

    return CostData(
        project=project,
        location=location,
        collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        manifest_sha256=manifest_sha256,
        nodes={c.uid: c for c in costs},
        leaf_weight={u: w[0] for u, w in weights.items() if w is not None and w[0] != 1},
        leaf_bytes={u: w[1] for u, w in weights.items() if w is not None},
    )
