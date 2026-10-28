"""Tests for :mod:`mael_domain.sarif`, against two committed SARIF fixtures.

The fixtures are hand-written in the shape of a Claude Security scan (three
findings) and a ZAP ``sarif-json`` report (two). A real scan would carry a
private project's findings into this repo.
"""

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from mael_domain import sarif

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sarif"
CLAUDE = (FIXTURES / "claude-security.sarif").read_text()
ZAP = (FIXTURES / "zap.sarif.json").read_text()


def _zap_variant(change: Callable[[dict[str, Any]], None]) -> str:
    """The ZAP fixture with ``change`` applied to its first run."""
    parsed = json.loads(ZAP)
    change(parsed["runs"][0])
    return json.dumps(parsed)


def _keys(text: str) -> list[str]:
    return [f.key for f in sarif.parse(text).findings]


class TestParse:
    def test_a_report_names_its_tool_and_its_scan_date(self):
        # The Claude Security scan wrote no invocation start; its generated_at dates it.
        assert (sarif.parse(CLAUDE).tool, sarif.parse(CLAUDE).date) == (
            "claude-security",
            "2026-10-27",
        )
        assert (sarif.parse(ZAP).tool, sarif.parse(ZAP).date) == ("zap", "2026-10-26")

    def test_an_unreadable_date_is_no_date(self):
        def misdated(run):
            run["invocations"][0]["startTimeUtc"] = "yesterday"

        assert sarif.parse(_zap_variant(misdated)).date is None

    def test_a_report_with_no_date_has_none(self):
        def undated(run):
            del run["invocations"]

        assert sarif.parse(_zap_variant(undated)).date is None

    @pytest.mark.parametrize(
        "text",
        [
            "not json",
            "{}",
            '{"runs": []}',
            "[]",
            '{"runs": [{"results": ["not an object"]}]}',
            '{"runs": [{"tool": {"driver": {"rules": [3]}}}]}',
        ],
    )
    def test_text_that_is_not_a_sarif_run_is_refused(self, text):
        with pytest.raises(ValueError):
            sarif.parse(text)


class TestFindings:
    def test_a_claude_security_finding_carries_its_plugin_fields(self):
        findings = sarif.parse(CLAUDE).findings
        assert [(f.label, f.level) for f in findings] == [
            ("F1", "error"),
            ("F2", "warning"),
            ("F3", "note"),
        ]
        title = "Search column interpolated into the SELECT list"
        assert findings[0] == sarif.Finding(
            key="v3:" + "1" * 64,
            label="F1",
            level="error",
            rule="CWE-89",
            cwe="CWE-89",
            title=title,
            file="app/tools/search.py",
            line=149,
            symbol="SearchTool.call",
            snippet='    f"SELECT {columns}"',
            detail={
                "description": f"How {title} happens.",
                "impact": f"Impact of {title}.",
                "exploit": f"An attacker exploits {title}.",
                "preconditions": "The route is reachable\nThe caller is signed in",
                "recommendation": f"Fix {title}.",
                "message": f"{title}.\n\nHow {title} happens.\n\nImpact: Impact of {title}.",
            },
        )

    def test_a_zap_finding_is_labelled_by_position_and_titled_by_its_rule(self):
        findings = sarif.parse(ZAP).findings
        assert [(f.label, f.level, f.rule, f.cwe) for f in findings] == [
            ("F1", "warning", "10038", "CWE-693"),
            ("F2", "error", "40012", "CWE-79"),
        ]
        xss = findings[1]
        assert xss == sarif.Finding(
            key=xss.key,
            label="F2",
            level="error",
            rule="40012",
            cwe="CWE-79",
            title="Cross Site Scripting (Reflected)",
            file="https://localhost:3560/search?q=x",
            line=1,
            symbol="",
            snippet="<script>alert(1)</script>",
            detail={
                "description": "Reflected XSS.",
                "recommendation": "Encode the output.",
                "message": "Cross Site Scripting (Reflected)",
            },
        )

    def test_a_rule_tag_gives_the_cwe(self):
        def tagged(run):
            rule = run["tool"]["driver"]["rules"][1]
            del rule["relationships"]
            rule["properties"]["tags"] = ["security", "external/cwe/cwe-80"]

        assert sarif.parse(_zap_variant(tagged)).findings[1].cwe == "CWE-80"


class TestFindingKey:
    def test_a_zap_key_ignores_the_host_the_port_and_the_line(self):
        # Each worktree serves on its own port, and an edit above a finding
        # moves its line. Neither makes it a different finding.
        def move(run):
            for result in run["results"]:
                loc = result["locations"][0]["physicalLocation"]
                loc["artifactLocation"]["uri"] = loc["artifactLocation"]["uri"].replace(
                    "https://localhost:3560", "http://127.0.0.1:4100"
                )
                loc["region"]["startLine"] = 40

        assert _keys(ZAP) == _keys(_zap_variant(move))
        assert len(set(_keys(ZAP))) == 2

    def test_a_sarif_fingerprint_is_the_key(self):
        def fingerprint(run):
            run["results"][0]["partialFingerprints"] = {"primaryLocationHash": "abc"}

        assert _keys(_zap_variant(fingerprint))[0] == "abc"

    def test_two_results_with_one_seed_get_apart_keys(self):
        # One rule on two parameters of one URL hashes alike.
        def twice(run):
            run["results"].append(json.loads(json.dumps(run["results"][1])))

        first, second = _keys(_zap_variant(twice))[1:]
        assert second == f"{first}-2"
