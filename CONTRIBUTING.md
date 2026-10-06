# Contributing

```bash
uv sync --extra dev --locked
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy
```

- Never send anything to BigQuery except dry-runs and the one `INFORMATION_SCHEMA` SELECT.
  `BigQueryBackend` raises on anything else. Tests use `FakeBackend`; none of them needs credentials.
- When you change the optimizer, make sure the brute-force cross-checks in `tests/property/` still pass
  (`HAKARI_FUZZ=1000 uv run pytest tests/property` runs more random projects).
- Do not put real project or table names into tests or fixtures; use made-up ones. A test checks the
  repository for personal data; names of your own organisation can go in an untracked
  `.forbidden-terms` file (one regex per line) or in `$HAKARI_FORBIDDEN_TERMS`.
- The lowest supported versions are tested with
  `uv venv && uv pip install --resolution lowest-direct -e ".[dev]"`.
- Commit messages follow Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`),
  written in English, with the reason for the change in the body when it is not obvious.

## Design in one minute

- `graph.py`, `optimize/ir.py` and other in-memory structures are plain (frozen) dataclasses.
- Everything that crosses the boundary of the process (config files, `.hakari/*.json`, `--format json`)
  is a pydantic model (`models.py`) with constrained scalar types (`units.py`, built on `Annotated`),
  so bad input is reported with the field name instead of failing later.
- `optimize/problem.py` states what is optimized (`Problem`, and `evaluate`, the definition of the
  cost); `optimize/builder.py` turns it into a MILP and must agree with `evaluate`. `optimize/sweep.py`
  (`prepare`, `optimize`) is shared by `optimize`, `explain` and `report`.
- The BigQuery backend, the cost model and the solver are `Protocol`s injected from `cli.py`.
- Only `store.py` reads and writes files; only `report.py` formats output for people.
