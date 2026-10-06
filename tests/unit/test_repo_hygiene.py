"""The repository must not carry real project names or personal data."""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Patterns that must never be committed. Generic ones are listed here. Names of the
# organisations and projects the author works with are NOT written in this public file: put them
# (one regex per line) in the untracked file `.forbidden-terms` or in $HAKARI_FORBIDDEN_TERMS.
GENERIC = [
    r"[\w.+-]+@(?!users\.noreply\.github\.com|example\.(com|net|org)|[\w-]+\.test\b)[\w-]+\.(com|net|org|jp)",
    r"/Users/\w+/",
    r"AIza[0-9A-Za-z_-]{20}",
]


def _forbidden() -> re.Pattern[str]:
    extra = os.environ.get("HAKARI_FORBIDDEN_TERMS", "").split(",")
    local = ROOT / ".forbidden-terms"
    if local.exists():
        extra += local.read_text().splitlines()
    return re.compile("|".join([*GENERIC, *(t.strip() for t in extra if t.strip())]), re.IGNORECASE)


FORBIDDEN = _forbidden()
SKIP = {".venv", ".git", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def test_no_real_names_in_tracked_files():
    me = Path(__file__).resolve()
    hits = []
    for path in ROOT.rglob("*"):
        if (
            path == me
            or path.name == ".forbidden-terms"
            or not path.is_file()
            or SKIP & set(path.relative_to(ROOT).parts)
            or path.suffix in {".png", ".pyc", ".lock"}
        ):
            continue
        if FORBIDDEN.search(path.read_text(errors="ignore")):
            hits.append(str(path.relative_to(ROOT)))
    assert not hits, hits
