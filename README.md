<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img src="docs/assets/logo.svg" alt="dbt-hakari" width="140">
  </picture>

  # dbt-hakari (秤)

  **Check BigQuery's billing formula against your real bill, then find the dbt views worth materializing with exact optimization.**

  ![python](https://img.shields.io/badge/python-3.10%2B-1f3a5f?logo=python&logoColor=white)
  ![license](https://img.shields.io/badge/license-MIT-2a6f4f)
  ![status](https://img.shields.io/badge/status-alpha-c8372d)
  ![solver](https://img.shields.io/badge/solver-HiGHS%20(MILP)-3c5f8f)
  ![mypy](https://img.shields.io/badge/mypy-strict-2a6f4f)
  ![ruff](https://img.shields.io/badge/lint-ruff-d7ff64?labelColor=1f3a5f)
  ![bigquery](https://img.shields.io/badge/BigQuery-on--demand-4285f4?logo=googlebigquery&logoColor=white)

  **English** | [日本語](README.ja.md)
</div>

---

For dbt projects on BigQuery with on-demand pricing, dbt-hakari finds **which views to turn into tables so the daily bill is smallest**, using integer programming. Before optimizing, it checks that the billing formula holds for *your* project by comparing it with your real job history (the trust gate).

Status: alpha. "Hakari" (秤) is a Japanese balance scale: it weighs what you pay, then weighs the alternatives.

> [!NOTE]
> dbt-hakari is **not published on PyPI**. Install the wheel from the [GitHub release](https://github.com/kiwamizamurai/dbt-hakari/releases/latest) (see [Install](#install)). `pip install dbt-hakari` will not find it.

## Why

On-demand billing is, to a good approximation:

```
billed bytes = max(bytes processed, 10 MiB × referenced tables, 10 MiB)
```

A dbt view is expanded to its base tables every time it is read. Every test and every downstream model on a view therefore pays the 10 MiB-per-table minimum again. A view over 3 tables read by 2 tests is already cheaper as a table (in general: T tables, n readers, materializing wins when n·T > T + n).

Which views to materialize is a combinatorial problem. Greedy selection is not optimal, and the optimal sets are not nested as the budget K grows. dbt-hakari solves it exactly as a MILP with HiGHS.

## Install

dbt-hakari is not on PyPI. Install the wheel from the [latest GitHub release](https://github.com/kiwamizamurai/dbt-hakari/releases/latest):

```bash
uv tool install https://github.com/kiwamizamurai/dbt-hakari/releases/download/v0.1.0/dbt_hakari-0.1.0-py3-none-any.whl
# or
pipx install https://github.com/kiwamizamurai/dbt-hakari/releases/download/v0.1.0/dbt_hakari-0.1.0-py3-none-any.whl
# or, inside a virtual environment
pip install https://github.com/kiwamizamurai/dbt-hakari/releases/download/v0.1.0/dbt_hakari-0.1.0-py3-none-any.whl
```

To try it without installing, or to run the unreleased `main`:

```bash
uvx --from git+https://github.com/kiwamizamurai/dbt-hakari hakari --help
```

Python 3.10+. It does not depend on `dbt-core`: it reads `manifest.json` as plain JSON. `hakari` and `dbt-hakari` are the same command. For development, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Usage

Prerequisites: a `target/manifest.json` from `dbt compile` (not just `dbt parse`: the compiled SQL is needed; dbt-core 1.5+, manifest schema v9+), and BigQuery permissions equivalent to `bigquery.jobs.create` and `bigquery.jobs.listAll`.

### 1. collect

```bash
dbt-hakari collect --manifest target/manifest.json \
  --project my-project --location asia-northeast2
```

- Dry-runs every model and test (free) to learn bytes processed and referenced tables.
- Reads the last 14 days of `INFORMATION_SCHEMA.JOBS_BY_PROJECT` with **one SELECT**. That is the only billed step: about 0.5 GiB for 14 days (inside the 1 TiB monthly free tier), capped by `maximum_bytes_billed` (default 1 GiB; the query fails instead of billing more). Use `--no-history` to skip it.
- Saves everything under `.hakari/` (`--data-dir` to change).

> [!TIP]
> The history read is the only billed step (about 0.5 GiB for 14 days, inside the 1 TiB monthly free tier) and is capped at 1 GiB. Use `--no-history` to skip it and work from dry-runs alone.

The tool issues nothing to BigQuery except dry-runs and that one SELECT; anything else raises an error.

### 2. verify

```bash
dbt-hakari verify --manifest target/manifest.json
```

```
trust gate: PASS
 graph == dry-run table counts  100.0% of 32 nodes
 bill within 5% of the formula  100.0% of 32 nodes
 median |error|                 0.00%
```

- Layer 1: the table count derived from the dbt graph must equal the dry-run's referenced tables. Mismatching nodes (for example views reading `INFORMATION_SCHEMA`) are left out, with the reason.
- Layer 2: `max(bytes, 10 MiB × tables)` must match the real bill. The comparison uses the largest bill of the last `--recent-days` (default 3) days, so partial runs such as `dbt run --empty` do not distort it.
- PASS, WARN or FAIL. Reservation (slot) billing is a FAIL: the model is meaningless there.

### 3. optimize

```bash
dbt-hakari optimize --manifest target/manifest.json --max-k 8 --compare-greedy
```

```
 K  GiB/day  saving  per month  greedy GiB/day
 0   0.52     0%     0.00 USD   -
 1   0.44    17%     0.02 USD   0.44 (+0.00)
 3   0.36    32%     0.03 USD   0.37 (+0.01)
 8   0.36    32%     0.03 USD   0.38 (+0.02)
unconstrained optimum: 0.36 GiB/day (32%) with 2 views

recommended: materialize 2 view(s)
```

(Example output.)

- Solves for K = 0, 1, 2, ... and recommends the K where one more view stops paying off.
- `--compare-greedy` shows how far greedy selection falls behind.
- `--assume-daily` counts every query once a day; otherwise run frequency is estimated from history.
- Narrow the search: `--exclude 'tag:x'`, `--exclude 'path:models/adhoc/*'`, `--force-table`, `--force-view`.
- A failed gate refuses with exit code 4. `--allow-unverified` overrides it, with a warning.

## Use it in CI

`verify` and `optimize` take `--format json|markdown`. The result goes to stdout alone and messages go to stderr, so the output can be piped or posted as is.

```bash
dbt-hakari verify --format json > verification.json           # exit 4 when the trust gate fails
dbt-hakari optimize --format markdown >> "$GITHUB_STEP_SUMMARY"
```

Exit codes are listed below. `verify --strict` also exits 10 on WARN. When the gate fails, `optimize` still prints the verification (in the chosen format) before exiting 4.

## Keep some views as views (`.hakari-ignore`)

One selector per line, in the syntax of `--exclude`. Text after ` #` is the reason, shown in the plan.

```
int_orders          # read by an external BI tool
tag:keep_view
path:models/adhoc/*
```

Use `--ignore-file` to point at another file.

## Configuration (`hakari.toml` or `pyproject.toml`)

```toml
[bigquery]
project = "my-project"
location = "asia-northeast2"
price_per_tib = 6.25
currency = "USD"
lookback_days = 14

[optimize]
max_k = 8
exclude = ["path:models/adhoc/*"]
```

Settings are read from `--config`, else `hakari.toml`, else the `[tool.dbt-hakari]` table of `pyproject.toml` (same keys, e.g. `[tool.dbt-hakari.optimize]`).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 2 | usage error |
| 3 | manifest unreadable |
| 4 | trust gate FAIL |
| 5 | BigQuery error |
| 6 | solver hit its time limit (best feasible solution returned) |
| 10 | `--strict` and WARN |

## Limits

> [!WARNING]
> Savings are **estimates**. Measure your bill after you change a view.

- The model assumes materializing a view does not change its readers' bytes processed (a view that benefited from predicate pushdown may scan more once materialized).
- BigQuery on-demand pricing and dbt only. Editions, reservations and BI Engine are out of scope (detected and flagged).
- Minimum billing unit per the official docs: 10 MiB per referenced table, 10 MiB per query.
- Wildcard-table minimum billing is unverified.

## Development

```bash
.venv/bin/python -m pytest      # unit tests and brute-force cross-checks (hypothesis)
.venv/bin/ruff check src tests scripts
.venv/bin/mypy
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## How it works (short)

A binary variable $x_v$ marks each view to materialize. Reachability $r(n,u)$ of the tables each query reads is expressed by inequalities, and billed bytes satisfy $c_n \ge 10\,\mathrm{MiB}\cdot T_n$ and $c_n \ge B_n$. The cost of building each materialized view is added, gated by $x_v$. Minimizing the total under $\sum_v x_v \le K$ is solved with `scipy.optimize.milp` (HiGHS).

## License

[MIT](LICENSE)
