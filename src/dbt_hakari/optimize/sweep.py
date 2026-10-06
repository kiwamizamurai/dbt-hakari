"""Sweep K = 0, 1, 2, ...: the best selection of at most K changes, and what each K saves."""

from __future__ import annotations

from dataclasses import dataclass

from dbt_hakari.cost.base import CostModel
from dbt_hakari.costdata import CostData
from dbt_hakari.errors import SolverFailed, UsageError
from dbt_hakari.graph import Graph
from dbt_hakari.history import History
from dbt_hakari.optimize.builder import MIB, build_model
from dbt_hakari.optimize.eligibility import select_candidates
from dbt_hakari.optimize.greedy import greedy
from dbt_hakari.optimize.options import Direction, OptimizeOptions
from dbt_hakari.optimize.plan import Change, KPoint, Plan, Recommendation
from dbt_hakari.optimize.problem import Candidate, Problem, evaluate
from dbt_hakari.optimize.queries import build_problem
from dbt_hakari.optimize.solvers.base import Solver
from dbt_hakari.verify import VerificationReport

GAIN_THRESHOLD = 0.02  # a change is worth it when what is left to gain is more than 2% of the bill


@dataclass
class Prepared:
    """Everything the commands share: what may change, and the problem built around it."""

    problem: Problem
    candidates: dict[str, Candidate]
    excluded: dict[str, str]


def prepare(
    graph: Graph,
    cost_data: CostData,
    history: History | None,
    cost_model: CostModel,
    options: OptimizeOptions,
    verification: VerificationReport | None = None,
) -> Prepared:
    candidates, excluded = select_candidates(graph, cost_data, history, options, verification)
    skip = frozenset(verification.excluded) if verification else frozenset()
    problem = build_problem(
        graph,
        cost_data,
        history,
        cost_model,
        candidates,
        skip=skip,
        assume_daily=options.assume_daily,
    )
    return Prepared(problem, candidates, excluded)


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
    prepared = prepare(graph, cost_data, history, cost_model, options, verification)
    problem, candidates, excluded = prepared.problem, prepared.candidates, prepared.excluded
    for uid in (*force_table, *force_view):
        if uid not in candidates:
            raise UsageError(
                f"{uid} cannot be forced: {excluded.get(uid, 'it is not a candidate')}"
            )
    baseline_tables = problem.baseline

    def cost_of(flips: frozenset[str]) -> float:
        return evaluate(problem, problem.tables_from_flips(flips))

    baseline = evaluate(problem, baseline_tables)
    notes: list[str] = []

    def point(flips: frozenset[str], k: int) -> KPoint:
        objective = cost_of(flips)
        return KPoint(
            k=k,
            objective_bytes_per_day=objective,
            saving_pct=100.0 * (baseline - objective) / baseline if baseline else 0.0,
            chosen=tuple(sorted(flips)),
            to_table=tuple(sorted(f for f in flips if f not in baseline_tables)),
            to_view=tuple(sorted(f for f in flips if f in baseline_tables)),
        )

    def solve(max_changes: int | None) -> KPoint:
        model = build_model(
            problem, max_changes=max_changes, force_table=force_table, force_view=force_view
        )
        result = solver.solve(model.ir, options.time_limit_s, options.mip_rel_gap)
        if result.values is None:
            raise SolverFailed(
                f"the solver found no solution ({result.status.value}): raise --time-limit, or "
                "narrow the search with --exclude or --max-k"
            )
        tables = model.selection(result.values)
        flips = problem.flips(tables)
        found = point(flips, len(flips) if max_changes is None else max_changes)
        assert result.objective is not None
        tie = 1e-3 * (len(tables - baseline_tables) - len(tables & baseline_tables))
        solver_bytes = (result.objective - tie) * MIB
        if abs(solver_bytes - found.objective_bytes_per_day) > 1e-4 * max(baseline, 1.0):
            notes.append(
                f"K={max_changes}: solver objective and independent evaluation differ "
                f"({solver_bytes:.0f} vs {found.objective_bytes_per_day:.0f} bytes/day)"
            )
        found.solver_status = result.status
        found.mip_gap = result.gap
        return found

    max_k = min(options.max_k, len(candidates))
    greedy_path = greedy(list(candidates), cost_of, max_k) if compare_greedy else None
    curve = [point(frozenset(), 0)]
    for k in range(1, max_k + 1):
        found = solve(k)
        if greedy_path is not None:
            found.greedy_objective_bytes_per_day = cost_of(greedy_path[k])
            found.greedy_chosen = tuple(sorted(greedy_path[k]))
        curve.append(found)
    unconstrained = solve(None) if candidates else None

    recommended_k = recommend(curve, baseline)
    chosen = frozenset(curve[recommended_k].chosen)
    recommendations = sorted(
        (
            Recommendation(
                uid=v,
                name=graph.nodes[v].name,
                change=Change.TO_VIEW if v in baseline_tables else Change.TO_TABLE,
                saving_bytes_per_day=cost_of(chosen - {v}) - cost_of(chosen),
            )
            for v in chosen
        ),
        key=lambda r: -r.saving_bytes_per_day,
    )
    if options.assume_daily or history is None:
        notes.append("every query is assumed to run once a day (--assume-daily or no history)")
    constant = sum(1 for q in problem.queries if q.kappa is None)
    if constant:
        notes.append(
            f"{constant} of {len(problem.queries)} queries read tables whose size is unknown, so "
            "their bytes are taken as a constant: changing what they read does not change their "
            "cost (collect again to measure the tables)"
        )
    notes += assumptions_that_matter(problem, options)
    if any(r.change is Change.TO_VIEW for r in recommendations):
        notes.append(
            "turning a table into a view is estimated cautiously (every read is assumed to "
            "recompute what the table's SQL scans), and was checked on a controlled experiment, "
            "not on your project: change one table, compare the next days' bill, then go on"
        )
    month = 30 * baseline
    after = 30 * curve[recommended_k].objective_bytes_per_day
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
        n_queries=len(problem.queries),
        currency=cost_model.currency,
        price_per_month_now=cost_model.price(month),
        price_per_month_after=cost_model.price(after),
        tib_per_month_now=month / 1024**4,
        external_bytes_per_day=external_bytes(problem),
        free_tib_per_month=cost_model.free_tib_per_month,
        unconstrained=unconstrained,
        notes=notes,
    )


def external_bytes(problem: Problem) -> float:
    """What queries from outside dbt cost per day today."""
    from dbt_hakari.optimize.problem import bill

    total = 0.0
    for q in problem.queries:
        if q.key.startswith("external:"):
            reads, floor = problem.walk(q.parents, problem.baseline)
            total += q.weight * bill(problem, q, reads, floor)
    return total


def recommend(curve: list[KPoint], baseline: float) -> int:
    """The smallest number of changes that gets within 2% of the best result on the curve, or 0
    when even the best result saves less than that. Looking at the whole curve (not only at the
    next step) finds changes that pay off only together."""
    best = min(p.objective_bytes_per_day for p in curve)
    margin = GAIN_THRESHOLD * baseline
    if baseline - best <= margin:
        return 0
    return next(p.k for p in curve if p.objective_bytes_per_day <= best + margin)


def assumptions_that_matter(problem: Problem, options: OptimizeOptions) -> list[str]:
    notes = []
    views = [c for c in problem.candidates.values() if not c.baseline_table]
    if views and options.output_ratio == 1.0 and not options.sizes:
        notes.append(
            "a view that becomes a table is assumed to be as large as the bytes its query "
            "processes (--output-ratio 1.0): no byte reduction is credited. Give --output-ratio "
            "or [sizes] if tables are much smaller than what builds them"
        )
    if options.direction is not Direction.VIEWS:
        notes.append(
            "tables become views only when everyone who reads them is known: dbt models, tests "
            "and the queries seen in the job history"
        )
    return notes
