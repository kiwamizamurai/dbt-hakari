# Changelog

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
