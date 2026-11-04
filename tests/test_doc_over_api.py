"""``mael doc`` as a thin client, against a real orchestrator.

As in ``test_cli_over_api.py``: a real ``build_app`` on loopback, and the CLI
on the test's thread. The Task document rules are tested in the domain suite;
this proves the wire and what the commands print.
"""

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from served_orchestrator import PROJECT, seed

from mael_cli.doc_cli import doc
from mael_domain import sarif

pytestmark = pytest.mark.binds_socket

ZAP = (
    Path(__file__).parents[1]
    / "lib"
    / "domain"
    / "fixtures"
    / "sarif"
    / "zap.sarif.json"
)


def mael_doc(*args: str):
    return CliRunner().invoke(
        doc, [*args, "--project", PROJECT], catch_exceptions=False
    )


@pytest.fixture
def served(table, start_server, monkeypatch):
    seed(table, title="Scan", id="NORT-7")
    seed(table, title="Fix", id="NORT-8")
    start_server()
    monkeypatch.setenv("MAEL_TASK_ID", "NORT-7")


def test_add_prints_the_versioned_uri_and_names_a_sarif_file_by_its_basename(
    served, tmp_path
):
    scan = tmp_path / "zap.sarif.json"
    scan.write_text(ZAP.read_text())

    first = mael_doc("add", str(scan))
    again = mael_doc("add", str(scan))

    assert first.exit_code == 0, first.output
    assert first.output == again.output == f"{PROJECT}:NORT-7:zap.sarif.json@v1\n"


def test_show_lists_a_sarif_document_s_findings_by_the_key_that_links_them(
    served, tmp_path
):
    scan = tmp_path / "zap.sarif.json"
    scan.write_text(ZAP.read_text())
    mael_doc("add", str(scan))

    xss = sarif.parse(ZAP.read_text()).findings[1].key

    shown = mael_doc("show", "zap.sarif.json").output
    linked = mael_doc("link", f"zap.sarif.json#{xss}", "NORT-8")

    assert (
        "  F2  error  Cross Site Scripting (Reflected)  "
        f"https://localhost:3560/search?q=x:1\n    #{xss}\n"
    ) in shown
    assert linked.exit_code == 0, linked.output
    assert f"#{xss}: {PROJECT}/NORT-8" in mael_doc("show", "zap.sarif.json").output


def test_show_prints_a_markdown_document_its_versions_and_links(served, tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("# Notes\n\n## Install\n")
    mael_doc("add", str(notes), "--name", "docs/notes.md")

    linked = mael_doc("link", "docs/notes.md#install", "NORT-8")
    shown = mael_doc("show", "docs/notes.md")
    body = mael_doc("show", "NORT-7:docs/notes.md@v1", "--body")

    assert linked.exit_code == 0, linked.output
    assert shown.output == (
        f"uri:      {PROJECT}:NORT-7:docs/notes.md\n"
        "title:    Notes\n"
        "kind:     markdown\n"
        f"owner:    {PROJECT}/NORT-7\n"
        "archived: no\n"
        "\nVersions:\n"
        "  v1  2026-09-01T00:00:00Z  draft  Notes\n"
        "\nLinked by:\n"
        f"  #install: {PROJECT}/NORT-8\n"
    )
    assert body.output == "# Notes\n\n## Install\n"


def test_a_refusal_is_the_server_s_message(served, tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("# Notes\n")
    mael_doc("add", str(notes))

    result = mael_doc("link", "notes.md#nope", "NORT-8")

    assert result.exit_code == 1
    assert "has no fragment #nope" in result.output


def test_a_file_in_a_worktree_is_named_by_its_path_there(served, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".drafts").mkdir()
    notes = tmp_path / ".drafts" / "verification.md"
    notes.write_text("# Shown\n")

    result = mael_doc("add", str(notes))

    assert result.output == f"{PROJECT}:NORT-7:.drafts/verification.md@v1\n"


def test_list_hides_an_archived_document_unless_asked(served, tmp_path):
    for name in ("kept.md", "done.md"):
        (tmp_path / name).write_text(f"# {name}\n")
        mael_doc("add", str(tmp_path / name))

    archived = mael_doc("archive", "done.md")
    shown = mael_doc("list").output
    every = mael_doc("list", "--archived").output

    assert archived.exit_code == 0, archived.output
    assert shown == f"Owned:\n  {PROJECT}:NORT-7:kept.md@v1  markdown  kept.md\n"
    assert every == (
        "Owned:\n"
        f"  {PROJECT}:NORT-7:done.md@v1  markdown  done.md  (archived)\n"
        f"  {PROJECT}:NORT-7:kept.md@v1  markdown  kept.md\n"
    )


def test_unarchive_and_unlink_undo_archive_and_link(served, tmp_path):
    (tmp_path / "a.md").write_text("# A\n")
    mael_doc("add", str(tmp_path / "a.md"))
    mael_doc("archive", "a.md")
    mael_doc("link", "a.md", "NORT-8")

    unarchived = mael_doc("unarchive", "a.md")
    unlinked = mael_doc("unlink", "a.md", "NORT-8")
    shown = mael_doc("show", "a.md").output

    assert (unarchived.exit_code, unlinked.exit_code) == (0, 0)
    assert "archived: no\n" in shown
    assert "Linked by" not in shown


def test_another_task_cannot_archive_it(served, tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("# A\n")
    mael_doc("add", str(tmp_path / "a.md"))
    monkeypatch.setenv("MAEL_TASK_ID", "NORT-8")

    result = mael_doc("archive", "NORT-7:a.md")

    assert result.exit_code == 1
    assert "does not own" in result.output
