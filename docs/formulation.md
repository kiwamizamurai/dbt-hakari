# Formulation, assumptions and review log

This page states exactly what dbt-hakari optimizes, why the MILP is valid, and what it does **not**
model. Code: `optimize/queries.py` (the queries), `optimize/builder.py` (the MILP),
`optimize/evaluate.py` (an independent evaluator used as the test oracle).

## Problem

Given a dbt project, choose a set $S$ of views to materialize as tables, $|S| \le K$, to minimize
the billed bytes per day on BigQuery on-demand pricing.

A *query* $q$ is one of: a table/incremental model build, a group of identical tests, or the build
of a view $v$ *if* $v \in S$. Each has a weight $m_q$ (runs per day) and a total bytes processed
$B_q$ (from a dry-run). Reading a view expands it to its parents; reading a table or source does
not. Let $T_q(S)$ be the number of distinct tables $q$ reads once the views in $S$ are tables.

$$\text{cost}(S) = \sum_q m_q \cdot \max\bigl(B_q,\; u\,T_q(S),\; u\bigr), \qquad u = 10\ \text{MiB}$$

where a build query of $v$ counts only if $v \in S$.

## MILP

Variables: $x_v \in \{0,1\}$ (view $v$ is a table); $r_{q,w} \in [0,1]$ ($q$ reads node $w$);
$z_{q,v} \in [0,1]$ ($q$ reads materialized view $v$, which then counts as one table); $c_q \ge 0$
(billed bytes of $q$).

$$\min \sum_q m_q\,c_q + \varepsilon \sum_v x_v \quad\text{s.t.}$$

- $r_{q,p} = 1$ for each direct parent $p$ of $q$ (for the build of $v$: $r_{q,p} \ge x_v$)
- $r_{q,w} \ge r_{q,u} - x_u$ for every parent $w$ of a view $u$ (a view that is not a table forwards the read)
- $z_{q,v} \ge r_{q,v} + x_v - 1$
- $c_q \ge u\,\bigl(\sum_{\text{leaves } w} \omega_w r_{q,w} + \sum_{v} z_{q,v}\bigr)$, $\;c_q \ge \max(B_q, u)$
  (for a build: $c_q \ge \max(B_q,u)\,x_v$)
- $\sum_v x_v \le K$

$\omega_w$ is the number of tables behind a wildcard leaf (1 otherwise). $\varepsilon$ is a tiny
tie-break (1 KiB) so that the solution is deterministic.

## Why this is valid

1. **No big-M, no binary for the max.** Linearizing $X = \max\{x_1, x_2\}$ in general needs big-M and
   an extra binary. Here $c_q$ appears with a positive coefficient in a *minimization* objective,
   so the two lower bounds $c_q \ge \dots$ are enough: the optimum sets $c_q$ to the maximum. This is
   the standard "conditional constraints can be dropped when the cost is minimized" argument.
2. **One-sided product linearization.** $z = r\,x$ for binaries needs $z \le x$, $z \le r$ and
   $z \ge r + x - 1$. Since $z$ also only increases the cost, $z \ge r + x - 1$ (with $z \ge 0$) suffices.
3. **$r$ is integral without being declared integer.** Its lower bounds are $1$, $0$, or
   $r_u - x_u$ with $x_u \in \{0,1\}$, so the minimizing value is always $0$ or $1$.
4. **Reachability is exact.** $r_{q,w}$ is forced to 1 exactly when some path from $q$ to $w$
   crosses only non-materialized views. Several paths to the same table are counted once
   (the constraints are lower bounds, so $r$ takes the maximum), matching "distinct tables".
5. **Independent check.** `tests/property/test_milp_vs_bruteforce.py` compares the MILP with
   exhaustive enumeration through `evaluate`, which expands the graph straight from the definition,
   on random DAGs (hundreds of examples per run).

Greedy selection has no useful guarantee here. The classic greedy guarantee for view selection
assumes a monotone benefit; a materialized view has a build cost, so benefit is not monotone, and
the optimal sets are not nested. This is why `--compare-greedy` exists.

## Assumptions and known limits

| # | Assumption | Effect when it is wrong |
|---|---|---|
| 1 | Billed bytes $= \max(B, uT, u)$ | See below: optimistic for a mix of large and small tables |
| 2 | Materializing a view does not change its readers' bytes processed $B_q$ | Predicate pushdown or column pruning can make the real saving smaller or larger. The tool says "estimate" |
| 3 | A view that becomes a table is built as often as the project's table models (median runs per day, or once a day with `--assume-daily`) | Wrong if views would be refreshed on a different schedule |
| 4 | Query cache hits, storage cost, freshness are not modeled | Cache hits are excluded from history; storage and freshness are out of scope |
| 5 | The 1 TiB monthly free tier is not applied | If the project is under the free tier, the real saving in money is 0 |
| 6 | Reservation (slot) billing is out of scope | Detected by `verify`, which fails |

### The per-table minimum (assumption 1)

The BigQuery documentation says the minimum billed for each referenced table is 10 MiB regardless
of the table's size, and the minimum per query is 10 MiB. Read literally, a query reading tables
with scanned bytes $b_t$ is billed $\sum_t \max(b_t, u)$. dbt-hakari uses $\max(\sum_t b_t, uT, u)$.
The two are equal when every $b_t \le u$ (the case that dominates small tests: $uT$) or every
$b_t \ge u$ (the total). They differ when a query reads one large and several small tables: the
literal rule adds up to $u$ for each small table, so the model is optimistic about the cost of those
extra tables and therefore conservative about the saving from removing them.

A dry-run returns only the total bytes, not $b_t$, so the literal rule cannot be applied.
`verify` compares the formula with the real bill of every node and prints a note when some nodes
were billed *more* than predicted.

To test this on your own project, run `hakari verify` and look at that note, or compare, for one
query over a large and a small table, the dry-run bytes with `total_bytes_billed` in
`INFORMATION_SCHEMA.JOBS_BY_PROJECT`.

## Review log (2026-10-06)

Checked against: the BigQuery pricing page and the "estimate and control costs" page (wording of the
per-table and per-query minimum), lecture notes on linearization (products of binaries, max
constraints, conditional constraints in minimization), and the view-selection literature.

- **Found and fixed:** the build frequency of a materialized view was fixed at once a day even
  when the history showed the project runs several times a day. It now follows the table models.
- **Found and documented:** the per-table minimum is applied to the total (above); `verify` now
  prints a note when it matters.
- **Confirmed:** the one-sided epigraph and product linearizations, integrality of $r$, and the
  agreement with brute force.
- **Not verified:** the formula on projects other than one real project; the effect of assumption 2.

## References

- BigQuery pricing, on-demand compute: <https://cloud.google.com/bigquery/pricing>
- BigQuery, estimate and control costs: <https://cloud.google.com/bigquery/docs/best-practices-costs>
- V. Harinarayan, A. Rajaraman, J. D. Ullman, *Implementing data cubes efficiently*, SIGMOD 1996.
  <https://doi.org/10.1145/233269.233333>
- H. Gupta, I. S. Mumick, *Selection of views to materialize under a maintenance cost constraint*,
  ICDT 1999. <https://link.springer.com/chapter/10.1007/3-540-49257-7_28>
- *Index and materialized view selection in data warehouses* (survey; the problem is NP-hard).
  <https://arxiv.org/abs/1701.08029>
- *An integer programming approach for the view and index selection problem*, Data & Knowledge
  Engineering. <https://www.sciencedirect.com/science/article/abs/pii/S0169023X12001000>
- MIT 1.041, lecture 22, integer programs (conditional constraints in a minimization objective).
  <https://web.mit.edu/1.041/spring2023/lectures/L22-integer-programs-2023sp.pdf>
- Q.-T. Luu, *Linearization techniques in linear programming*.
  <https://luuquangtrung.github.io/blog/2021/linearization-notes/>
- L. C. Coelho, *Linearization of the product of two variables*.
  <https://www.leandro-coelho.com/linearization-product-variables/>
