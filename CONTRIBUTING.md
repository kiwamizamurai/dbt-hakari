# Contributing

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/ruff check src tests scripts && .venv/bin/ruff format src tests scripts
.venv/bin/mypy
```

- Never send anything to BigQuery except dry-runs and the one `INFORMATION_SCHEMA` SELECT.
  `BigQueryBackend` raises on anything else.
- When you change the optimizer, make sure the brute-force cross-checks in `tests/property/` still pass.
- Do not put real project or table names into tests or fixtures; use made-up ones.
- Commit messages follow Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`),
  written in English, with the reason for the change in the body when it is not obvious.

## Design in one minute

- `graph.py`, `optimize/ir.py` and other in-memory structures are plain (frozen) dataclasses.
- Everything that crosses the boundary of the process (config files, `.hakari/*.json`, `--format json`)
  is a pydantic model (`models.py`) with constrained scalar types (`units.py`, built on `Annotated`),
  so bad input is reported with the field name instead of failing later.
- The BigQuery backend, the cost model and the solver are `Protocol`s injected from `cli.py`.
- Only `store.py` reads and writes files; only `report.py` formats output for people.
