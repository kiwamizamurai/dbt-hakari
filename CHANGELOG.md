# Changelog

## 0.2.0 (2026-10-06)

- **Both directions**: tables can be recommended to become views, not only views to become
  tables. A table only becomes a view when everyone who reads it is known (dbt readers, queries
  seen in the job history, no exposure). `--direction views|tables|both`.
- **Bytes**: each query reads a fitted fraction of every table it touches, so the bill follows
  what a model's materialization does to its readers. A table that is expensive to build is
  assumed to cost what its SQL scans every time it is read as a view. `--output-ratio` and
  `[sizes]` give the size of tables that do not exist yet.
- **Recommendation**: the smallest number of changes within 2% of the best, so changes that only
  work together are found (the old rule stopped at the first flat step).
- **Greedy baseline**: it now stops when no addition helps. It was forced to add harmful views,
  which exaggerated its gap to the optimum.
- **Who ran what**: jobs are counted per user. By default only what service accounts ran is
  counted (dbt jobs, and queries from apps and BI services) when people ran jobs too: laptop
  runs and ad-hoc analysis are not the schedule. `hakari users`,
  `--only-user`, `--exclude-user`, `--all-users`.
- **What it is worth**: the plan states the bill per month, the saving, the free tier, how much of
  the bill comes from outside dbt, and says when nothing is worth changing.
- **verify** points out models that were probably changed during the history window.
- A view that becomes a table is built as often as the project's table models, not once a day.
- **`hakari report`**: where the bill goes, every daily query at today's setup, grouped and ranked.
- **`hakari explain <model>`**: who reads a model, what each reader pays now and after the change,
  and why the model is, or is not, worth changing.
- First-use mistakes now end in a sentence: `--location US` was refused (only lower case was
  accepted); settings in `hakari.toml` such as `lookback_days` were ignored by the commands;
  an empty manifest, missing credentials, a wrong region, an unsolvable model, a damaged or
  older data file and a typo in a selector prefix gave a stack trace or a misleading FAIL.
- Every saved file carries a `schema_version`. Queries read by many relations count once per user.
- Dependencies: lower bounds now match what is tested (`typer>=0.15.4`, `google-cloud-bigquery>=3.25`,
  `pydantic>=2.7`, `scipy>=1.11`); the unused `highs` extra is gone.
- Checked on controlled BigQuery experiments: the minimum applies to the query as a whole, not
  per table, and reading a view costs less than the full computation (the model errs on the safe
  side).

## 0.1.0 (2026-10-06)

- `collect`, `verify` and `optimize` commands.
- Trust gate for the billing formula: table counts from the dbt graph are checked against dry-runs,
  and the formula is checked against the real bill.
- Exact optimization of which views to materialize (MILP with HiGHS), with a greedy baseline for comparison.
- `--format json|markdown` for CI; results go to stdout and messages to stderr.
- Manifest validation: schemas older than v9 are refused, and a manifest without compiled SQL is
  refused or warned about.
- `.hakari-ignore` pins views that must stay views, with a reason; settings are also read from
  `[tool.dbt-hakari]` in `pyproject.toml`.
- Config and saved data are validated with pydantic and `Annotated` constraints; settings such as
  `tolerance` and `min_match` now reach the trust gate.
- CI installs from `uv.lock`.
