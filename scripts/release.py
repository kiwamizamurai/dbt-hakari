"""Build the package and publish a GitHub release for the version in ``__init__.py``.

Run by .github/workflows/release.yml, or by hand with the GitHub CLI logged in:

    python scripts/release.py            # builds, tags and publishes
    python scripts/release.py --dry-run  # builds and prints what it would publish

It refuses to run when the version is already released, so a release is made once.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "kiwamizamurai/dbt-hakari"


def version_of(root: Path = ROOT) -> str:
    text = (root / "src" / "dbt_hakari" / "__init__.py").read_text()
    match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit("cannot find __version__ in src/dbt_hakari/__init__.py")
    return match.group(1)


def changelog_section(changelog: str, version: str) -> str:
    """The text under ``## <version> (...)`` up to the next ``## `` heading."""
    pattern = rf"^## {re.escape(version)}\b[^\n]*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, changelog, re.MULTILINE | re.DOTALL)
    if match is None or not match.group(1).strip():
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    return match.group(1).strip()


def checksums(files: list[Path]) -> str:
    return "\n".join(
        f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}" for f in sorted(files)
    )


def notes(version: str, changelog: str, sums: str) -> str:
    wheel = f"https://github.com/{REPO}/releases/download/v{version}/dbt_hakari-{version}-py3-none-any.whl"
    return f"""dbt-hakari {version}.

> [!NOTE]
> dbt-hakari is **not published on PyPI**. Install the wheel attached to this release.

## Install

```bash
uv tool install {wheel}
# or: pipx install <same url>   /   pip install <same url>
```

Python 3.10+. Then run `hakari collect`, `hakari verify` and `hakari optimize` (see the README).

## Changes

{changelog_section(changelog, version)}

## Checksums (SHA-256)

```
{sums}
```
"""


def run(*command: str) -> str:
    return subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True).stdout


def already_released(tag: str) -> bool:
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", REPO], cwd=ROOT, capture_output=True, text=True
    )
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="build and print, publish nothing")
    args = parser.parse_args()

    version = version_of()
    tag = f"v{version}"
    if not args.dry_run and already_released(tag):
        raise SystemExit(f"{tag} is already released: bump __version__ and CHANGELOG.md first")

    dist = ROOT / "dist"
    for old in dist.glob("*"):
        old.unlink()
    run("uv", "build", "--out-dir", str(dist))
    files = sorted([*dist.glob("*.whl"), *dist.glob("*.tar.gz")])
    if len(files) != 2:
        raise SystemExit(f"expected a wheel and an sdist in dist/, found {[f.name for f in files]}")
    sums = checksums(files)
    (dist / "SHA256SUMS").write_text(sums + "\n")
    body = notes(version, (ROOT / "CHANGELOG.md").read_text(), sums)

    if args.dry_run:
        print(body)
        return
    notes_file = dist / "RELEASE_NOTES.md"
    notes_file.write_text(body)
    sha = run("git", "rev-parse", "HEAD").strip()
    run(
        "gh", "release", "create", tag, *map(str, files), str(dist / "SHA256SUMS"),
        "--repo", REPO, "--target", sha, "--title", f"dbt-hakari {version}",
        "--notes-file", str(notes_file),
    )  # fmt: skip
    print(f"released {tag} at {sha[:7]}: https://github.com/{REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    sys.exit(main())
