"""A small synthetic dbt project whose dry-run results and job history are derived from a
single ground truth, so verification outcomes can be asserted exactly."""

from __future__ import annotations

from dataclasses import replace

from dbt_hakari.backends import DryRunResult, FakeBackend, JobRecord
from dbt_hakari.graph import Graph, Node, NodeKind

MIB = 1024**2


def sql_of(uid: str) -> str:
    return f"select * from {uid}"


def build_world(
    n_tests: int = 25,
    runs_per_test: int = 3,
    billed_per_table: int = 10 * MIB,
    test_bytes: int = MIB,
    **job_overrides,
) -> tuple[Graph, FakeBackend]:
    sources = ["s0", "s1", "s2"]
    nodes = [Node(s, s, NodeKind.SOURCE, relation=f"`p`.`d`.`{s}`") for s in sources]
    nodes.append(
        Node("V", "V", NodeKind.VIEW_MODEL, parents=tuple(sources), compiled_code=sql_of("V"))
    )
    for i in range(n_tests):
        nodes.append(
            Node(f"t{i}", f"t{i}", NodeKind.TEST, parents=("V",), compiled_code=sql_of(f"t{i}"))
        )
    graph = Graph({n.uid: n for n in nodes})

    tables = frozenset({"p.d.s0", "p.d.s1", "p.d.s2"})
    results = {sql_of("V"): DryRunResult(2 * MIB, tables)}
    for i in range(n_tests):
        results[sql_of(f"t{i}")] = DryRunResult(test_bytes, tables)
    for s in sources:
        results[f"SELECT * FROM `p`.`d`.`{s}`"] = DryRunResult(MIB, frozenset({f"p.d.{s}"}))

    jobs = []
    for i in range(n_tests):
        for _ in range(runs_per_test):
            job = JobRecord(node_id=f"t{i}", billed_bytes=3 * billed_per_table)
            jobs.append(replace(job, **job_overrides) if job_overrides else job)
    return graph, FakeBackend(results, jobs)
