"""A ``.hakari-ignore`` file: views that must stay views, one selector per line.

    # comments and blank lines are ignored
    int_orders              # kept as a view: read by an external tool
    tag:keep_view
    path:models/adhoc/*

The selector syntax is the one of ``--exclude``. Text after `` #`` is the reason, shown in the
plan next to the view it excluded.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_FILE = Path(".hakari-ignore")


def load_suppressions(path: Path | None) -> dict[str, str]:
    """selector -> reason (empty when none was written)."""
    if path is None or not path.exists():
        return {}
    found: dict[str, str] = {}
    for line in path.read_text().splitlines():
        selector, _, reason = line.partition(" #")
        selector = selector.strip()
        if selector and not selector.startswith("#"):
            found[selector] = reason.strip()
    return found
