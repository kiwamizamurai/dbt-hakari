"""Sweep K = 0, 1, 2, ...: the best selection of at most K views, and how much each K saves."""

from __future__ import annotations

from dbt_hakari.cost.base import CostModel
from dbt_hakari.costdata import CostData
from dbt_hakari.graph import Graph
from dbt_hakari.history import History
from dbt_hakari.optimize.builder import MIB, build_model
from dbt_hakari.optimize.eligibility import select_candidates
from dbt_hakari.optimize.evaluate import evaluate
from dbt_hakari.optimize.greedy import greedy
from dbt_hakari.optimize.options import OptimizeOptions
from dbt_hakari.optimize.plan import KPoint, Plan, Recommendation
from dbt_hakari.optimize.queries import build_queries
from dbt_hakari.optimize.solvers.base import Solver
from dbt_hakari.verify import VerificationReport

GAIN_THRESHOLD = 0.02  # stop recommending more views when one more saves < 2% of the baseline


def optimize(
    graph: Graph,
    cost_data: CostData,
    history: History | None,
    cost_model: CostModel,
    options: OptimizeOptions,
    *,
    solver: Solver,
    verification: VerificationReport | None = None,
    compare_greedy: bool = False,
    force_table: tuple[str, ...] = (),
    force_view: tuple[str, ...] = (),
) -> Plan:
    candidates, excluded = select_candidates(
        graph, cost_data, list(options.exclude), options.exclude_reasons, verification
    )
    skip = frozenset(verification.excluded) if verification else frozenset()
    queries = build_queries(
        graph, cost_data, history, candidates, skip=skip, assume_daily=options.assume_daily
    )
    leaf_weight = cost_data.leaf_weight

    def cost_of(selection: frozenset[str]) -> float:
        return evaluate(selection, queries, graph, leaf_weight, cost_model)

    baseline = cost_of(frozenset())
    notes: list[str] = []

    def solve(max_views: int | None) -> KPoint:
        model = build_model(
            graph,
            queries,
            candidates,
            leaf_weight,
            cost_model,
            max_views=max_views,
            force_table=force_table,
            force_view=force_view,
        )
        result = solver.solve(model.ir, options.time_limit_s, options.mip_rel_gap)
        if result.values is None:
            raise RuntimeError(f"solver returned no solution (status={result.status})")
        chosen = model.selection(result.values)
        objective = cost_of(chosen)
        assert result.objective is not None
        solver_obj = result.objective - 1e-3 * len(chosen)  # remove the tie-break term
        if abs(solver_obj * MIB - objective) > 1e-4 * max(objective, 1.0):
            notes.append(
                f"K={max_views}: solver objective and independent evaluation differ "
                f"({solver_obj * MIB:.0f} vs {objective:.0f} bytes/day)"
            )
        return KPoint(
            k=len(chosen) if max_views is None else max_views,
            objective_bytes_per_day=objective,
            saving_pct=100.0 * (baseline - objective) / baseline if baseline else 0.0,
            chosen=tuple(sorted(chosen)),
            solver_status=result.status,
            mip_gap=result.gap,
        )

    max_k = min(options.max_k, len(candidates))
    greedy_path = greedy(candidates, cost_of, max_k) if compare_greedy else None
    curve = [KPoint(k=0, objective_bytes_per_day=baseline, saving_pct=0.0, chosen=())]
    for k in range(1, max_k + 1):
        point = solve(k)
        if greedy_path is not None:
            g = greedy_path[k]
            point.greedy_objective_bytes_per_day = cost_of(g)
            point.greedy_chosen = tuple(sorted(g))
        curve.append(point)
    unconstrained = solve(None) if candidates else None

    recommended_k = 0
    for k in range(1, len(curve)):
        gain = curve[k - 1].objective_bytes_per_day - curve[k].objective_bytes_per_day
        if baseline and gain >= GAIN_THRESHOLD * baseline:
            recommended_k = k
        else:
            break
    chosen = frozenset(curve[recommended_k].chosen)
    recommendations = sorted(
        (
            Recommendation(
                uid=v,
                name=graph.nodes[v].name,
                saving_bytes_per_day=cost_of(chosen - {v}) - cost_of(chosen),
            )
            for v in chosen
        ),
        key=lambda r: -r.saving_bytes_per_day,
    )
    if options.assume_daily or history is None:
        notes.append("every query is assumed to run once a day (--assume-daily or no history)")
    return Plan(
        baseline_bytes_per_day=baseline,
        curve=curve,
        recommended_k=recommended_k,
        recommendations=recommendations,
        excluded=excluded,
        assumptions=cost_model.describe_assumptions(),
        assume_daily=options.assume_daily or history is None,
        verification_verdict=verification.verdict if verification else None,
        n_candidates=len(candidates),
        n_queries=len(queries),
        unconstrained=unconstrained,
        notes=notes,
    )
