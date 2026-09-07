"""The dead-code gate's two passes must agree on their settings.

``bin/vulture-check`` restates every ``[tool.vulture]`` setting as a
command-line flag, because vulture reads one table and command-line values
replace it. Drift between the two fails silently: add a decorator to the table,
forget the script, and the test pass reports phantom Click commands. Nothing
errors; the gate gets the answer wrong. See docs/dev/dead-code.md.
"""

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "vulture-check"


def _table() -> dict:
    with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
        return tomllib.load(handle)["tool"]["vulture"]


def _flag(name: str) -> str:
    """The value the script passes to one long-form flag."""
    text = SCRIPT.read_text()
    match = re.search(rf"--{name}=(?:'([^']*)'|(\S+))", text)
    assert match, f"bin/vulture-check passes no --{name}"
    return match.group(1) or match.group(2)


def test_the_test_pass_keeps_every_ignored_decorator() -> None:
    passed = set(_flag("ignore-decorators").split(","))
    missing = set(_table()["ignore_decorators"]) - passed
    assert not missing, (
        f"bin/vulture-check drops {sorted(missing)} from --ignore-decorators, "
        "so the test pass reports them as dead."
    )


def test_the_test_pass_keeps_every_ignored_name() -> None:
    passed = set(_flag("ignore-names").split(","))
    missing = set(_table()["ignore_names"]) - passed
    assert not missing, (
        f"bin/vulture-check drops {sorted(missing)} from --ignore-names, "
        "so the test pass reports them as dead."
    )


def test_both_passes_use_one_confidence_level() -> None:
    assert int(_flag("min-confidence")) == _table()["min_confidence"], (
        "The passes disagree on min_confidence, so one reports findings the "
        "other hides."
    )


def test_the_test_pass_reads_the_whitelist() -> None:
    """A whitelist the test pass cannot see makes its entries look dead."""
    whitelist = "\n".join(
        p for p in _table()["paths"] if p.endswith(".vulture_whitelist.py")
    )
    assert whitelist, "[tool.vulture] paths names no whitelist"
    assert whitelist in SCRIPT.read_text(), (
        f"bin/vulture-check does not pass {whitelist} to the test pass."
    )
