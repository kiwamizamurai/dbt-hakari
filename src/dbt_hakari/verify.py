"""The trust gate.

Before recommending anything, check that the model matches this project's reality:

* layer 1 (structure): the number of tables the graph says a query reads equals what BigQuery's
  dry-run reports;
* layer 2 (money): ``max(bytes processed, 10 MiB x tables)`` equals what BigQuery actually
  billed for the same node, using only non-cached, successful jobs.
"""

from __future__ import annotations

import statistics
from enum import Enum
from typing import Literal

from pydantic import Field

from dbt_hakari.cost.base import CostModel
from dbt_hakari.costdata import CostData
from dbt_hakari.errors import GateFailedError
from dbt_hakari.graph import FLAG_READS_INFORMATION_SCHEMA, Graph, NodeKind
from dbt_hakari.history import History
from dbt_hakari.models import FrozenModel, Model
from dbt_hakari.units import Bytes, Count, Ratio


class Verdict(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


_ORDER = {Verdict.PASS: 0, Verdict.WARN: 1, Verdict.FAIL: 2}


def _worst(*verdicts: Verdict) -> Verdict:
    return max(verdicts, key=lambda v: _ORDER[v])


class GateThresholds(FrozenModel):
    tolerance: Ratio = 0.05
    graph_pass: Ratio = 0.90
    graph_warn: Ratio = 0.80
    bill_pass: Ratio = 0.90
    bill_warn: Ratio = 0.75
    min_samples: Count = 20
    warn_samples: Count = 5
    median_pass: Ratio = 0.02
    median_warn: Ratio = 0.05


class CheckStatus(str, Enum):
    OK = "ok"
    TABLE_MISMATCH = "table_mismatch"
    BILL_MISMATCH = "bill_mismatch"
    NO_HISTORY = "no_history"
    EXCLUDED = "excluded"


class NodeCheck(FrozenModel):
    uid: str
    graph_tables: Count
    dryrun_tables: Count
    table_match: bool
    predicted_billed: Bytes
    actual_billed: Bytes | None
    rel_err: float | None  # (actual - predicted) / predicted; > 0 means the bill is higher
    status: CheckStatus
    reason: str | None = None


class VerificationReport(Model):
    schema_version: Literal[1] = 1
    checks: dict[str, NodeCheck] = Field(default_factory=dict)
    graph_match_rate: float = 0.0
    n_graph_checked: Count = 0
    bill_within_tol_rate: float | None = None
    n_bill_samples: Count = 0
    overestimate_rate: float | None = None
    underestimate_rate: float | None = None
    median_abs_err: float | None = None
    stale_suspects: list[str] = Field(default_factory=list)  # billed far more than today's SQL
    excluded: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    verdict: Verdict = Verdict.WARN
    thresholds: GateThresholds = GateThresholds()


def _graph_verdict(rate: float, t: GateThresholds) -> Verdict:
    if rate >= t.graph_pass:
        return Verdict.PASS
    return Verdict.WARN if rate >= t.graph_warn else Verdict.FAIL


def _bill_verdict(report: VerificationReport, t: GateThresholds) -> Verdict:
    if report.bill_within_tol_rate is None or report.n_bill_samples < t.warn_samples:
        report.notes.append(
            f"only {report.n_bill_samples} billed samples: the billing formula could not be "
            "verified, so the result is a structural check only"
        )
        return Verdict.WARN
    rate = report.bill_within_tol_rate
    verdict = (
        Verdict.PASS
        if rate >= t.bill_pass
        else (Verdict.WARN if rate >= t.bill_warn else Verdict.FAIL)
    )
    median = report.median_abs_err or 0.0
    median_verdict = (
        Verdict.PASS
        if median <= t.median_pass
        else (Verdict.WARN if median <= t.median_warn else Verdict.FAIL)
    )
    verdict = _worst(verdict, median_verdict)
    if report.n_bill_samples < t.min_samples:
        report.notes.append(
            f"{report.n_bill_samples} billed samples (< {t.min_samples}): low confidence"
        )
        verdict = _worst(verdict, Verdict.WARN)
    return verdict


def verify(
    graph: Graph,
    cost_data: CostData,
    history: History | None,
    cost_model: CostModel,
    thresholds: GateThresholds | None = None,
    recent_days: int = 3,
) -> VerificationReport:
    t = thresholds or GateThresholds()
    report = VerificationReport(thresholds=t)
    report.notes.append(
        f"actual bill = the largest billed bytes of the last {recent_days} days with jobs "
        "(partial runs such as `dbt run --empty` are ignored)"
    )

    n_graph = n_graph_ok = 0
    errors: list[float] = []
    over = under = within = 0
    far_off: list[tuple[float, str]] = []
    under_ids: list[str] = []
    for uid, cost in sorted(cost_data.nodes.items()):
        node = graph.nodes.get(uid)
        if node is None or node.kind is NodeKind.SOURCE:
            continue
        if cost.error:
            report.excluded[uid] = f"dry-run failed: {cost.error}"
            report.checks[uid] = NodeCheck(
                uid=uid,
                graph_tables=0,
                dryrun_tables=0,
                table_match=False,
                predicted_billed=0,
                actual_billed=None,
                rel_err=None,
                status=CheckStatus.EXCLUDED,
                reason=cost.error,
            )
            continue
        graph_tables = graph.table_count(uid, cost_data.leaf_weight)
        predicted = cost_model.billed_bytes(cost.bytes_processed, cost.n_tables)
        match = graph_tables == cost.n_tables
        n_graph += 1
        n_graph_ok += match

        status, reason = CheckStatus.OK, None
        actual = history.recent_billed(uid, recent_days) if history is not None else None
        rel_err: float | None = None
        if not match:
            status = CheckStatus.TABLE_MISMATCH
            if FLAG_READS_INFORMATION_SCHEMA in node.flags:
                reason = "the SQL reads INFORMATION_SCHEMA directly (no dbt ref)"
            else:
                reason = (
                    f"graph says {graph_tables} tables, BigQuery says {cost.n_tables} "
                    "(conditional Jinja refs, wildcard tables or direct references?)"
                )
            report.excluded[uid] = reason
        elif actual is None:
            status = CheckStatus.NO_HISTORY
        else:
            rel_err = (actual - predicted) / predicted if predicted else None
            if actual > 2 * predicted and actual - predicted >= 100 * 1024**2:
                far_off.append((actual - predicted, uid))
            if rel_err is not None:
                errors.append(abs(rel_err))
                if abs(rel_err) <= t.tolerance:
                    within += 1
                elif rel_err < 0:
                    over += 1  # we predicted more than was billed
                    status = CheckStatus.BILL_MISMATCH
                else:
                    under += 1
                    under_ids.append(uid)
                    status = CheckStatus.BILL_MISMATCH
        report.checks[uid] = NodeCheck(
            uid=uid,
            graph_tables=graph_tables,
            dryrun_tables=cost.n_tables,
            table_match=match,
            predicted_billed=predicted,
            actual_billed=actual,
            rel_err=rel_err,
            status=status,
            reason=reason,
        )

    if far_off:
        far_off.sort(reverse=True)
        report.stale_suspects = [uid for _, uid in far_off]
        names = ", ".join(graph.nodes[uid].name for _, uid in far_off[:3])
        report.notes.append(
            f"{len(far_off)} node(s) were billed much more than today's SQL predicts "
            f"(e.g. {names}). They were probably changed during the history window, and their "
            "old runs are still in it. Compare again after a few days, or lower --recent-days"
        )
    report.n_graph_checked = n_graph
    report.graph_match_rate = n_graph_ok / n_graph if n_graph else 0.0
    report.n_bill_samples = len(errors)
    if errors:
        report.bill_within_tol_rate = within / len(errors)
        report.overestimate_rate = over / len(errors)
        report.underestimate_rate = under / len(errors)
        report.median_abs_err = statistics.median(errors)
        if under:
            names = ", ".join(graph.nodes[u].name for u in under_ids[:3])
            report.notes.append(
                f"{under} node(s) were billed more than the formula predicts (e.g. {names}). "
                "Look for work a dry-run cannot see: a MERGE or insert_overwrite into a large "
                "table, or SQL that changed since the history"
            )

    verdict = _graph_verdict(report.graph_match_rate, t) if n_graph else Verdict.FAIL
    verdict = _worst(verdict, _bill_verdict(report, t))
    if history is not None and history.uses_reservations:
        verdict = Verdict.FAIL
        report.notes.append(
            "jobs ran in a reservation (Editions / flat-rate): on-demand cost numbers are not "
            "meaningful for this project"
        )
    report.verdict = verdict
    return report


def require_trust(report: VerificationReport, *, allow_unverified: bool) -> bool:
    """Refuse to continue on a failed gate. Returns True when running on an unverified model."""
    if report.verdict is not Verdict.FAIL:
        return False
    if not allow_unverified:
        raise GateFailedError(
            "the billing model does not match this project's bill; refusing to recommend. "
            "Use --allow-unverified to run anyway."
        )
    return True
