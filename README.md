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

> [!NOTE]
> dbt-hakari is **not published on PyPI**. Install the wheel from the [latest GitHub release](https://github.com/kiwamizamurai/dbt-hakari/releases/latest); `pip install dbt-hakari` will not find it.

## Quick start

```bash
# 1. install (Python 3.10+)
uv tool install https://github.com/kiwamizamurai/dbt-hakari/releases/download/v0.1.0/dbt_hakari-0.1.0-py3-none-any.whl

# 2. in your dbt project (run `dbt compile` first: it needs target/manifest.json)
hakari collect  --project my-project --location asia-northeast2   # dry-runs + one capped history SELECT
hakari verify                                                      # does the billing formula match your real bill?
hakari optimize --max-k 8 --compare-greedy                         # which views to materialize
```

`pipx install` and `pip install` work with the same wheel URL. To try it without installing: `uvx --from git+https://github.com/kiwamizamurai/dbt-hakari hakari --help`. You need BigQuery permissions equivalent to `bigquery.jobs.create` and `bigquery.jobs.listAll`. `hakari` and `dbt-hakari` are the same command.

## What you get

```
trust gate: PASS
 bill within 5% of the formula  100.0% of 32 nodes

 K  GiB/day  saving  greedy GiB/day
 0   0.52     0%     -
 1   0.44    17%     0.44 (+0.00)
 2   0.36    32%     0.36 (+0.00)
 4   0.36    32%     0.38 (+0.02)

recommended: materialize 2 view(s)
  int_orders        saves 0.09 GiB/day
  int_order_lines   saves 0.08 GiB/day
```

(Example output of a synthetic project.) Set the recommended views to `materialized: table` in dbt, then check your bill.

> [!WARNING]
> Savings are **estimates**. The model assumes materializing a view does not change its readers' bytes processed. Measure your bill after you change a view.

## Why

BigQuery on-demand bills roughly `max(bytes processed, 10 MiB × referenced tables, 10 MiB)`. A dbt view is expanded to its base tables each time it is read, so every test and downstream model pays the per-table minimum again. A view over T tables read by n readers is cheaper as a table when n·T > T + n. Which views to materialize is a combinatorial problem: greedy selection is not optimal, and the best K+1 views do not contain the best K. dbt-hakari solves it exactly (MILP, HiGHS), after checking on *your* project that the formula matches your real bill (the trust gate).

"Hakari" (秤) is a Japanese balance scale: it weighs what you pay, then weighs the alternatives.

<details>
<summary><b>What each command does</b></summary>

- **collect**: dry-runs every model and test (free) and reads the last 14 days of `INFORMATION_SCHEMA.JOBS_BY_PROJECT` with **one SELECT**, the only billed step (about 0.5 GiB, inside the 1 TiB monthly free tier, capped at 1 GiB; the query fails instead of billing more). `--no-history` skips it. Results go to `.hakari/`. Nothing else is ever sent to BigQuery.
- **verify**: checks that table counts from the dbt graph equal the dry-runs' referenced tables, and that the formula matches your real bill (largest bill of the last `--recent-days`, default 3, so `dbt run --empty` does not distort it). PASS, WARN or FAIL (exit 4). Reservation (slot) billing is a FAIL.
- **optimize**: solves for K = 0, 1, 2, … views and recommends the K where one more view stops paying off. `--assume-daily` counts every query once a day; `--exclude 'tag:x'`, `--force-table`, `--force-view` narrow the search; `--allow-unverified` overrides a failed gate.

</details>

<details>
<summary><b>Use it in CI</b></summary>

`verify` and `optimize` take `--format json|markdown`: the result goes to stdout alone, messages to stderr.

```bash
hakari verify --format json > verification.json              # exit 4 when the trust gate fails
hakari optimize --format markdown >> "$GITHUB_STEP_SUMMARY"
```

`verify --strict` also exits 10 on WARN. When the gate fails, `optimize` still prints the verification before exiting 4.

</details>

<details>
<summary><b>Keep some views as views (<code>.hakari-ignore</code>)</b></summary>

One selector per line (the syntax of `--exclude`); text after ` #` is the reason, shown in the plan. Use `--ignore-file` for another path.

```
int_orders          # read by an external BI tool
tag:keep_view
path:models/adhoc/*
```

</details>

<details>
<summary><b>Configuration and exit codes</b></summary>

Settings come from `--config`, else `hakari.toml`, else `[tool.dbt-hakari]` in `pyproject.toml`. Bad values are reported with their name.

```toml
[bigquery]
project = "my-project"
location = "asia-northeast2"
price_per_tib = 6.25
lookback_days = 14

[optimize]
max_k = 8
exclude = ["path:models/adhoc/*"]
```

| Code | Meaning |
|---|---|
| 0 | success |
| 2 | usage error |
| 3 | manifest unreadable |
| 4 | trust gate FAIL |
| 5 | BigQuery error |
| 6 | solver hit its time limit (best feasible solution returned) |
| 10 | `--strict` and WARN |

</details>

<details>
<summary><b>Limits</b></summary>

- BigQuery on-demand pricing and dbt only. Editions, reservations and BI Engine are out of scope (detected and flagged). Needs dbt-core 1.5+ manifests (schema v9+) with compiled SQL.
- The billing minimum is 10 MiB per referenced table and per query (official docs). The model applies it to the total, which is optimistic for a mix of large and small tables. Wildcard-table minimum billing is unverified. See [docs/formulation.md](docs/formulation.md) for the model, its assumptions and the review log.
- Alpha: validated on one real project so far.

</details>

## Development

```bash
uv sync --extra dev
uv run pytest && uv run ruff check src tests && uv run mypy
```

See [CONTRIBUTING.md](CONTRIBUTING.md). [MIT](LICENSE).
