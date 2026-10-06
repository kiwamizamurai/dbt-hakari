<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img src="docs/assets/logo.svg" alt="dbt-hakari" width="140">
  </picture>

  # dbt-hakari (秤)

  **Check BigQuery's billing formula against your real bill, then find which dbt models are worth turning from view to table (or back) with exact optimization.**

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
uv tool install https://github.com/kiwamizamurai/dbt-hakari/releases/download/v0.2.0/dbt_hakari-0.2.0-py3-none-any.whl

# 2. in your dbt project (run `dbt compile` first: it needs target/manifest.json)
hakari collect  --project my-project --location US   # dry-runs + one capped history SELECT
hakari verify                                                      # does the billing formula match your real bill?
hakari report                                                      # where the bill goes
hakari optimize                                                    # which models to turn into tables, or back into views
hakari explain int_orders                                          # why one model is (not) worth changing
```

`pipx install` and `pip install` work with the same wheel URL. To try it without installing: `uvx --from git+https://github.com/kiwamizamurai/dbt-hakari hakari --help`. You need BigQuery permissions equivalent to `bigquery.jobs.create` and `bigquery.jobs.listAll`. `hakari` and `dbt-hakari` are the same command.

## What you get

```
5 models may change, 12 distinct daily queries
┏━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━┓
┃ changes ┃ GiB/day ┃ saving ┃ per month ┃
┡━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━┩
│ <= 0    │ 11.33   │ 0%     │ 0.00 USD  │
│ <= 1    │ 9.42    │ 17%    │ 0.35 USD  │
│ <= 2    │ 6.33    │ 44%    │ 0.92 USD  │
│ <= 4    │ 5.21    │ 54%    │ 1.12 USD  │
└─────────┴─────────┴────────┴───────────┘

today: 11.33 GiB/day = 2.07 USD/month (0.33 TiB; the first 1 TiB/month of the whole project is free)
recommended: 5.21 GiB/day (-54%, saves 1.12 USD/month)
  make a table: int_customers  saves 3.50 GiB/day
  make a table: int_orders  saves 2.32 GiB/day
  make a table: int_order_lines  saves 0.75 GiB/day
  make a table: mart_sales  saves 0.37 GiB/day
```

(Example output of a synthetic project.) Set the recommended models to `materialized: table` in dbt, then check your bill. When nothing is worth changing, it says so: most projects have little to gain.

> [!WARNING]
> Savings are **estimates**. Change one model, then compare the next days' bill before you change more.

## Why

BigQuery on-demand bills roughly `max(bytes processed, 10 MiB × referenced tables, 10 MiB)`. A dbt view is expanded to its base tables every time it is read, so every test and downstream model pays that again; a table is paid for once when it is built. Whether to materialize a model depends on how many tables it reads, how often, and who else reads the result. That is a combinatorial problem: greedy selection is not optimal, because changes can pay off only together.

```
 changes  GiB/day  saving  greedy GiB/day
 <= 1      0.28     0%     0.28 (+0.00)     no single change helps
 <= 2      0.23    17%     0.28 (+0.05)     but this pair does
```

dbt-hakari solves it exactly (MILP, HiGHS), in both directions, after checking on *your* project that the formula matches your real bill (the trust gate). "Hakari" (秤) is a Japanese balance scale: it weighs what you pay, then weighs the alternatives.

<details>
<summary><b>What each command does</b></summary>

- **collect**: dry-runs every model and test (free), measures the size of every table, and reads the last 14 days of `INFORMATION_SCHEMA.JOBS_BY_PROJECT` with **one SELECT**, the only billed step (about 0.6 GiB, inside the 1 TiB monthly free tier, capped at 1 GiB; the query fails instead of billing more). `--no-history` skips it. Results go to `.hakari/`. Nothing else is ever sent to BigQuery.
- **verify**: checks that table counts from the dbt graph equal the dry-runs' referenced tables, and that the formula matches your real bill (largest bill of the last `--recent-days`, default 3). PASS, WARN or FAIL (exit 4). It also points out models that were probably changed during the history window: their old runs are still in it.
- **report**: where the bill goes. Every daily query at today's setup, grouped (tests, builds, models, queries from outside dbt) and ranked, with the bill per month. `--top N`.
- **optimize**: solves for at most K = 0, 1, 2, ... changes and recommends the smallest K that gets within 2% of the best, so changes that only work together are found. It says what the result is worth in money and against the free tier.
- **explain** `<model>`: why a model is, or is not, worth changing. What reads it and what each reader pays now and after the change, the saving, the reason it is left alone if it is, and whether the last `optimize` recommended it.
- **users**: who ran the jobs in the history.

Cost monitoring over time is what packages like [dbt-bigquery-monitoring](https://github.com/bqbooster/dbt-bigquery-monitoring) are for. dbt-hakari answers a different question: what to change, and what it saves.

</details>

<details>
<summary><b><code>optimize</code> options</b></summary>

| Option | Meaning |
|---|---|
| `--max-k N` | at most N changes (default 8) |
| `--direction views\|tables\|both` | which changes may be recommended (default both) |
| `--exclude SELECTOR` | leave models alone: `tag:x`, `path:models/a/*`, `uid:model.pkg.*`, `name:int_*`, `package:pkg`, or a name glob. Repeatable. A typo in the prefix is an error |
| `--force-table UID`, `--force-view UID` | a candidate that must be a table, or must be a view |
| `--assume-daily` | count every query once a day, ignoring the history |
| `--compare-greedy` | add a column with what greedy selection reaches |
| `--time-limit S` | seconds per solve (default 60); exit 6 if there is no solution by then |
| `--allow-unverified` | go on although the trust gate failed |
| `--price-per-tib X` | on-demand price (default 6.25) |
| `--output-ratio R` | size of a view's table relative to the bytes its query scans (default 1.0) |
| `--only-user`, `--exclude-user`, `--all-users` | whose jobs count |
| `--format text\|json\|markdown` | output |

Most of them can also be set in `hakari.toml` (see below).

</details>

<details>
<summary><b>Both directions, and who else reads a table</b></summary>

`--direction views|tables|both` (default both). A view becomes a table when its readers pay for it again and again; a table becomes a view when it is cheap to build and read by few.

A table is only ever turned into a view when everyone who reads it is known: its dbt readers, and the queries apps and BI services ran against it, which `collect` reads from the job history. A table read by an exposure, or whose readers are unknown, is left alone. A view is assumed to recompute what its SQL scans each time it is read, so tables that are expensive to build stay tables.

How big a view's table would be is not known before it exists: `--output-ratio` (default 1.0: no reduction is credited) or a `[sizes]` table in the config gives the size of tables you know.

</details>

<details>
<summary><b>Laptops, schedules and <code>--all-users</code></b></summary>

A run from a laptop is not the schedule, and neither is an analyst's ad-hoc query. When service accounts and people both ran jobs, only what service accounts ran is counted by default (dbt jobs, and queries from apps and BI services), and it says so. `hakari users` lists who ran what. Choose yourself with `--only-user` and `--exclude-user` (addresses or globs such as `*@example.com`), or count everyone with `--all-users`.

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
<summary><b>Keep some models as they are (<code>.hakari-ignore</code>)</b></summary>

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
location = "US"
price_per_tib = 6.25
lookback_days = 14

[optimize]
max_k = 8
direction = "both"
output_ratio = 1.0
exclude = ["path:models/adhoc/*"]
exclude_users = ["*@example.com"]

[sizes]
int_orders = 120000000   # bytes of the table, if you know it
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
- Incremental, snapshot and seed models are treated as fixed: they are not recommended for change, and their cost is estimated from dry-runs, which can overstate an incremental run.
- The estimates are checked on controlled BigQuery experiments and on one real project, not on many.
- The first 1 TiB per month of a project is free: a saving is worth money only above it.

</details>

## Development

```bash
uv sync --extra dev
uv run pytest && uv run ruff check src tests && uv run mypy
```

See [CONTRIBUTING.md](CONTRIBUTING.md). [MIT](LICENSE).
