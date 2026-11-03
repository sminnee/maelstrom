"""Tests for :mod:`mael_domain.reports`, against two committed SARIF fixtures.

The fixtures are hand-written in the shape of a Claude Security scan (three
findings) and a ZAP ``sarif-json`` report (two). A real scan would carry a
private project's findings into this repo.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mael_domain import reports
from mael_domain.task_store import InMemoryStore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "reports"
CLAUDE = FIXTURES / "claude-security"
ZAP = FIXTURES / "zap" / "report.sarif.json"
NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


class TestImport:
    def test_a_claude_security_directory_lands_dated_by_its_scan(self, store):
        # The scan wrote no invocation start time; its own generated_at dates it.
        path = reports.import_report(store, "p", CLAUDE, now=NOW)
        assert path == "reports/2026-10-27-claude-security"
        assert sorted(store.list_dir("p/reports/")) == [
            "p/reports/2026-10-27-claude-security/CLAUDE-SECURITY-RESULTS.md",
            "p/reports/2026-10-27-claude-security/CLAUDE-SECURITY-RESULTS.sarif",
        ]
        # The files land as the tool wrote them.
        assert store.read(f"p/{path}/CLAUDE-SECURITY-RESULTS.md") == (
            (CLAUDE / "CLAUDE-SECURITY-RESULTS.md").read_text()
        )

    def test_a_zap_file_lands_dated_by_its_invocation(self, store):
        assert reports.import_report(store, "p", ZAP, now=NOW) == (
            "reports/2026-10-26-zap"
        )

    def test_a_second_report_of_the_same_day_gets_a_suffix(self, store):
        reports.import_report(store, "p", ZAP, now=NOW)
        assert reports.import_report(store, "p", ZAP, now=NOW) == (
            "reports/2026-10-26-zap-2"
        )

    def test_a_report_with_no_date_takes_the_import_time(self, store, tmp_path):
        sarif = json.loads(ZAP.read_text())
        del sarif["runs"][0]["invocations"]
        src = tmp_path / "report.sarif.json"
        src.write_text(json.dumps(sarif))
        assert reports.import_report(store, "p", src, now=NOW) == (
            "reports/2026-11-03-zap"
        )

    def test_a_dotfile_is_left_behind(self, store, tmp_path):
        # A Claude Security scan writes a `.gitignore` of `*`. Copied in, it
        # would hide the report from the notebook commit.
        shutil.copytree(CLAUDE, tmp_path / "scan")
        (tmp_path / "scan" / ".gitignore").write_text("*\n")
        path = reports.import_report(store, "p", tmp_path / "scan", now=NOW)
        assert not store.exists(f"p/{path}/.gitignore")

    def test_a_file_that_is_not_text_is_refused(self, store, tmp_path):
        # The notebook store holds text only.
        shutil.copytree(CLAUDE, tmp_path / "scan")
        (tmp_path / "scan" / "shot.png").write_bytes(b"\x89PNG\xff\xfe")
        with pytest.raises(ValueError, match="shot.png is not UTF-8"):
            reports.import_report(store, "p", tmp_path / "scan", now=NOW)
        assert store.list_dir("p/") == []

    def test_a_directory_with_no_sarif_is_refused(self, store, tmp_path):
        (tmp_path / "notes.md").write_text("# nothing")
        with pytest.raises(ValueError, match="SARIF"):
            reports.import_report(store, "p", tmp_path, now=NOW)

    def test_list_names_every_report(self, store):
        reports.import_report(store, "p", CLAUDE, now=NOW)
        reports.import_report(store, "p", ZAP, now=NOW)
        reports.import_report(store, "q", ZAP, now=NOW)
        assert [r.name for r in reports.list_reports(store, "p")] == [
            "2026-10-26-zap",
            "2026-10-27-claude-security",
        ]


def _zap_variant(tmp_path: Path, change) -> Path:
    """The ZAP fixture with ``change`` applied to its parsed SARIF."""
    sarif = json.loads(ZAP.read_text())
    change(sarif["runs"][0])
    src = tmp_path / f"variant-{len(list(tmp_path.iterdir()))}.sarif.json"
    src.write_text(json.dumps(sarif))
    return src


def _keys(store, report: str) -> list[str]:
    return [f.key for f in reports.read_findings(store, "p", report)]


class TestFindings:
    def test_a_claude_security_finding_carries_its_plugin_fields(self, store):
        reports.import_report(store, "p", CLAUDE, now=NOW)
        findings = reports.read_findings(store, "p", "2026-10-27-claude-security")
        assert [(f.label, f.level) for f in findings] == [
            ("F1", "error"),
            ("F2", "warning"),
            ("F3", "note"),
        ]
        title = "Search column interpolated into the SELECT list"
        assert findings[0] == reports.Finding(
            key="v3:" + "1" * 64,
            label="F1",
            ref="reports/2026-10-27-claude-security/CLAUDE-SECURITY-RESULTS.sarif#v3:"
            + "1" * 64,
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

    def test_a_zap_finding_is_labelled_by_position_and_titled_by_its_rule(self, store):
        reports.import_report(store, "p", ZAP, now=NOW)
        findings = reports.read_findings(store, "p", "2026-10-26-zap")
        assert [(f.label, f.level, f.rule, f.cwe) for f in findings] == [
            ("F1", "warning", "10038", "CWE-693"),
            ("F2", "error", "40012", "CWE-79"),
        ]
        xss = findings[1]
        assert xss == reports.Finding(
            key=xss.key,
            label="F2",
            ref=f"reports/2026-10-26-zap/report.sarif.json#{xss.key}",
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

    def test_a_report_path_reads_as_its_name_does(self, store):
        path = reports.import_report(store, "p", ZAP, now=NOW)
        assert _keys(store, path) == _keys(store, "2026-10-26-zap")


class TestFindingKey:
    def test_a_zap_key_ignores_the_host_the_port_and_the_line(self, store, tmp_path):
        # Each worktree serves on its own port, and an edit above a finding
        # moves its line. Neither makes it a different finding.
        def move(run):
            for result in run["results"]:
                loc = result["locations"][0]["physicalLocation"]
                loc["artifactLocation"]["uri"] = loc["artifactLocation"]["uri"].replace(
                    "https://localhost:3560", "http://127.0.0.1:4100"
                )
                loc["region"]["startLine"] = 40

        one = reports.import_report(store, "p", ZAP, now=NOW)
        two = reports.import_report(store, "p", _zap_variant(tmp_path, move), now=NOW)
        assert _keys(store, one) == _keys(store, two)
        assert len(set(_keys(store, one))) == 2

    def test_a_sarif_fingerprint_is_the_key(self, store, tmp_path):
        def fingerprint(run):
            run["results"][0]["partialFingerprints"] = {"primaryLocationHash": "abc"}

        report = reports.import_report(
            store, "p", _zap_variant(tmp_path, fingerprint), now=NOW
        )
        assert _keys(store, report)[0] == "abc"

    def test_two_results_with_one_seed_get_apart_keys(self, store, tmp_path):
        # One rule on two parameters of one URL hashes alike.
        def twice(run):
            run["results"].append(json.loads(json.dumps(run["results"][1])))

        report = reports.import_report(
            store, "p", _zap_variant(tmp_path, twice), now=NOW
        )
        first, second = _keys(store, report)[1:]
        assert second == f"{first}-2"


def test_a_rule_tag_gives_the_cwe(store, tmp_path):
    def tagged(run):
        rule = run["tool"]["driver"]["rules"][1]
        del rule["relationships"]
        rule["properties"]["tags"] = ["security", "external/cwe/cwe-80"]

    report = reports.import_report(store, "p", _zap_variant(tmp_path, tagged), now=NOW)
    assert reports.read_findings(store, "p", report)[1].cwe == "CWE-80"


class TestReferences:
    def test_a_label_resolves_to_the_finding_key(self, store):
        reports.import_report(store, "p", CLAUDE, now=NOW)
        assert reports.resolve_reference(
            store, "p", "2026-10-27-claude-security#F2"
        ) == (
            "reports/2026-10-27-claude-security/CLAUDE-SECURITY-RESULTS.sarif#v3:"
            + "2" * 64
        )

    def test_a_full_reference_resolves_to_itself(self, store):
        reports.import_report(store, "p", CLAUDE, now=NOW)
        ref = (
            "reports/2026-10-27-claude-security/CLAUDE-SECURITY-RESULTS.sarif#v3:"
            + "3" * 64
        )
        assert reports.resolve_reference(store, "p", ref) == ref

    def test_an_unknown_finding_is_refused(self, store):
        reports.import_report(store, "p", CLAUDE, now=NOW)
        with pytest.raises(ValueError, match="F9"):
            reports.resolve_reference(store, "p", "2026-10-27-claude-security#F9")

    def test_a_reference_with_no_finding_is_refused(self, store):
        with pytest.raises(ValueError, match="<report>#<finding>"):
            reports.resolve_reference(store, "p", "F1")

    def test_an_unknown_report_is_refused(self, store):
        with pytest.raises(ValueError, match="nope"):
            reports.resolve_reference(store, "p", "nope#F1")
