"""The release notes are built from CHANGELOG.md and the checksums of the built files."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release", ROOT / "scripts" / "release.py")
assert spec and spec.loader
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

CHANGELOG = """# Changelog

## 0.3.0 (2026-11-01)

- newer thing

## 0.2.0 (2026-10-06)

- first thing
- second thing

## 0.1.0 (2026-10-05)

- old thing
"""


def test_the_version_comes_from_the_package():
    from dbt_hakari import __version__

    assert release.version_of() == __version__


def test_only_the_section_of_that_version_is_taken():
    section = release.changelog_section(CHANGELOG, "0.2.0")
    assert section == "- first thing\n- second thing"
    assert "newer" not in section and "old thing" not in section


def test_a_version_without_a_changelog_entry_is_refused():
    with pytest.raises(SystemExit, match=r"no section for 0\.9\.9"):
        release.changelog_section(CHANGELOG, "0.9.9")


def test_the_real_changelog_has_an_entry_for_the_current_version():
    text = (ROOT / "CHANGELOG.md").read_text()
    assert release.changelog_section(text, release.version_of())


def test_checksums_are_sha256_per_file(tmp_path):
    (tmp_path / "b.whl").write_bytes(b"b")
    (tmp_path / "a.tar.gz").write_bytes(b"a")
    lines = release.checksums([tmp_path / "b.whl", tmp_path / "a.tar.gz"]).splitlines()
    assert [line.split()[1] for line in lines] == ["a.tar.gz", "b.whl"]  # sorted
    assert lines[0].startswith("ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb")


def test_the_notes_say_where_to_install_from_and_carry_the_checksums():
    body = release.notes("0.2.0", CHANGELOG, "abc  file.whl")
    assert "releases/download/v0.2.0/dbt_hakari-0.2.0-py3-none-any.whl" in body
    assert "not published on PyPI" in body and "abc  file.whl" in body
    assert "- first thing" in body


def test_the_workflow_only_calls_the_script():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "python3 scripts/release.py" in workflow
    assert "gh release" not in workflow  # the logic lives in the script, not in the workflow


def test_the_script_can_describe_a_release_without_publishing_it():
    # a dry run needs a build; check only that the command line is accepted
    result = subprocess.run(
        ["python3", str(ROOT / "scripts" / "release.py"), "--help"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert "--dry-run" in result.stdout
