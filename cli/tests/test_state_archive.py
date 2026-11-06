"""The State archive: export, the checks before an import, and the swap.

Real SQLite files under ``tmp_path``, migrated with this build's ladders. The
supervisor is a fake, so nothing reaches launchd or systemd.
"""

import asyncio
import hashlib
import json
import sqlite3
import stat
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from self_cli_support import FakeSupervisor

from mael_cli.self.host_service import Applied, HostService
from mael_cli.self.production import Production
from mael_cli.self.state_archive import ArchiveError, Import, export_state
from mael_domain.state_db.migrate import LADDERS, migrate_state_db
from mael_domain.state_db.migrations.spine import SPINE

NOW = datetime(2026, 11, 6, 9, 30, 15, tzinfo=UTC)
UNITS = [
    HostService(name=n, argv=("x",), env={}, cwd=Path("/"), log=Path("/l"))
    for n in ("orchestrator", "agent-daemon", "schedule")
]
STOP_START = [
    "remove orchestrator agent-daemon schedule",
    "apply orchestrator agent-daemon schedule",
]
VERSIONS = {name: len(rungs) for name, rungs in LADDERS.items()}


def make_state(root: Path, pages: dict[str, str] | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    asyncio.run(migrate_state_db(root / "state.db"))
    add_pages(root / "state.db", pages or {})


def add_pages(db: Path, pages: dict[str, str]) -> None:
    conn = sqlite3.connect(db)
    with conn:
        for page, body in pages.items():
            conn.execute(
                "INSERT INTO wiki_pages (id, revision, body) VALUES (?, 1, ?)",
                (page, body),
            )
    conn.close()


def pages(db: Path) -> dict[str, str]:
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute("SELECT id, body FROM wiki_pages").fetchall())
    finally:
        conn.close()


def export(root: Path, dest: Path) -> None:
    export_state(root, dest, host="old", now=NOW, maelstrom="v1.2.3", commit="abc")


def snapshot(root: Path) -> dict[str, str]:
    """Every file under ``root`` and its hash.

    A read-only open of a WAL database creates an empty ``-wal`` and ``-shm``,
    which hold no rows, so they are left out.
    """
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.name.endswith(("-wal", "-shm"))
    }


def write_zip(path: Path, members: dict) -> Path:
    """A zip of ``members``; a ``None`` value leaves that entry out."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            if data is not None:
                zf.writestr(name, data)
    return path


@dataclass
class Target:
    """The production root an import writes to, and what the import told."""

    prod: Production
    sup: FakeSupervisor = field(default_factory=FakeSupervisor)
    lines: list[str] = field(default_factory=list)

    @property
    def db(self) -> Path:
        return self.prod.state / "state.db"

    def importer(self, **kw) -> Import:
        return Import(
            prod=self.prod,
            supervisor=self.sup,
            units=UNITS,
            echo=self.lines.append,
            now=NOW,
            **kw,
        )

    def staging_left(self) -> list[Path]:
        return list(self.prod.state.glob(".import-*"))


@pytest.fixture
def source(tmp_path) -> Path:
    root = tmp_path / "source"
    make_state(root, {"style": "dry"})
    (root / "attachments" / "proj" / "t1").mkdir(parents=True)
    (root / "attachments" / "proj" / "t1" / "shot.png").write_bytes(b"png")
    (tmp_path / "outside.png").write_bytes(b"secret")
    (root / "attachments" / "link.png").symlink_to(tmp_path / "outside.png")
    return root


@pytest.fixture
def archive(source, tmp_path) -> Path:
    path = tmp_path / "x.zip"
    export(source, path)
    return path


@pytest.fixture
def target(tmp_path) -> Target:
    return Target(Production(tmp_path / "home"))


def test_round_trip(archive, target):
    target.importer()(archive)

    with zipfile.ZipFile(archive) as zf:
        assert sorted(zf.namelist()) == [
            "attachments/proj/t1/shot.png",
            "manifest.json",
            "state.db",
        ]
        assert json.loads(zf.read("manifest.json")) == {
            "format": 1,
            "maelstrom": "v1.2.3",
            "commit": "abc",
            "spine": len(SPINE),
            "ladders": VERSIONS,
            "created": "2026-11-06T09:30:15+00:00",
            "host": "old",
        }
    assert pages(target.db) == {"style": "dry"}
    # The symlink to a file outside the root stays behind.
    assert sorted(snapshot(target.prod.state / "attachments")) == ["proj/t1/shot.png"]
    assert target.sup.calls == STOP_START
    assert not target.staging_left()


def test_export_reads_a_row_that_is_only_in_the_wal(source, tmp_path):
    keep = sqlite3.connect(source / "state.db")
    keep.execute("PRAGMA journal_mode=WAL")
    keep.execute("PRAGMA wal_autocheckpoint=0")
    add_pages(source / "state.db", {"wal": ""})
    assert (source / "state.db-wal").stat().st_size > 0

    export(source, tmp_path / "x.zip")
    keep.close()

    with zipfile.ZipFile(tmp_path / "x.zip") as zf:
        zf.extract("state.db", tmp_path / "out")
    assert "wal" in pages(tmp_path / "out" / "state.db")


def test_export_refuses_an_existing_path(source, tmp_path):
    (tmp_path / "x.zip").write_bytes(b"mine")

    with pytest.raises(ArchiveError, match="exists"):
        export(source, tmp_path / "x.zip")

    assert (tmp_path / "x.zip").read_bytes() == b"mine"


def _manifest(**changes) -> str:
    data = {
        "format": 1,
        "maelstrom": "v1",
        "commit": None,
        "spine": len(SPINE),
        "ladders": VERSIONS,
        "created": "",
        "host": "old",
    }
    return json.dumps(data | changes)


def _symlink() -> zipfile.ZipInfo:
    info = zipfile.ZipInfo("attachments/link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


def _too_new_db(tmp_path: Path) -> bytes:
    """A real database whose tasks ladder is past this build's."""
    root = tmp_path / "newer"
    make_state(root)
    conn = sqlite3.connect(root / "state.db")
    with conn:
        conn.execute(
            "UPDATE schema_version SET version = version + 1 WHERE name = 'tasks'"
        )
    conn.close()
    return (root / "state.db").read_bytes()


# Each case passes `replace=True`, so only the archive checks can refuse: the
# unchanged root then proves they run before a change to the disk.
@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ({"manifest.json": _manifest(spine=len(SPINE) + 1)}, "spine schema"),
        (
            {"manifest.json": _manifest(ladders={"tasks": VERSIONS["tasks"] + 1})},
            "tasks schema is at version",
        ),
        ({"manifest.json": _manifest(ladders={"future": 1})}, "does not know"),
        ({"manifest.json": _manifest(format=2)}, "format 2"),
        ({"manifest.json": "{"}, "not JSON"),
        ({"manifest.json": "[]"}, "not a JSON object"),
        ({"manifest.json": json.dumps({"spine": 1})}, "no format version"),
        ({"manifest.json": _manifest(ladders={"tasks": "8"})}, "no valid schema"),
        ({"manifest.json": None}, "no manifest.json"),
        ({"attachments/../../evil": "x"}, "unsafe"),
        ({"/etc/evil": "x"}, "unsafe"),
        ({_symlink(): "/etc/passwd"}, "symlink"),
        ({"notes.txt": "x"}, "unknown entry"),
        ({"state.db": None}, "no state.db"),
        ({"state.db": "not sqlite at all"}, "not a database"),
        ({"state.db": "too-new"}, "state.db's tasks schema"),
    ],
)
def test_import_refuses_before_any_change(tmp_path, target, entries, message):
    make_state(target.prod.state, {"mine": "keep"})
    before = snapshot(target.prod.state)
    members = {"manifest.json": _manifest(), "state.db": "x"} | entries
    if members["state.db"] == "too-new":
        members["state.db"] = _too_new_db(tmp_path)
    archive = write_zip(tmp_path / "bad.zip", members)

    with pytest.raises(ArchiveError, match=message):
        target.importer(replace=True)(archive)

    assert snapshot(target.prod.state) == before
    assert target.sup.calls == []


def test_import_refuses_a_file_that_is_not_a_zip(tmp_path, target):
    (tmp_path / "x.zip").write_text("not a zip")

    with pytest.raises(ArchiveError, match="not a zip"):
        target.importer()(tmp_path / "x.zip")

    assert target.sup.calls == []


@pytest.mark.parametrize("held", ["rows", "attachments"])
def test_a_root_that_holds_state_needs_replace(archive, target, held):
    make_state(target.prod.state, {"mine": "keep"} if held == "rows" else {})
    if held == "attachments":
        (target.prod.state / "attachments").mkdir()
        (target.prod.state / "attachments" / "old.png").write_bytes(b"old")
    before = snapshot(target.prod.state)

    with pytest.raises(ArchiveError, match="--replace"):
        target.importer()(archive)

    assert snapshot(target.prod.state) == before
    assert target.sup.calls == []


def test_an_empty_migrated_root_needs_no_replace(archive, target):
    make_state(target.prod.state)

    target.importer()(archive)

    assert pages(target.db) == {"style": "dry"}
    assert not list(target.prod.state.glob("replaced-*"))


def test_a_row_written_before_the_stop_refuses_after_it(archive, target):
    make_state(target.prod.state)

    class WritesAsItStops(FakeSupervisor):
        def remove(self, specs):
            add_pages(target.db, {"late": "write"})
            return super().remove(specs)

    target.sup = WritesAsItStops()

    with pytest.raises(ArchiveError, match="--replace"):
        target.importer()(archive)

    assert pages(target.db) == {"late": "write"}
    assert target.sup.calls == STOP_START
    assert not target.staging_left()


def test_replace_keeps_the_old_state_aside(archive, target):
    make_state(target.prod.state, {"mine": "keep"})
    (target.prod.state / "state.db-wal").write_bytes(b"")
    (target.prod.state / "attachments").mkdir()
    (target.prod.state / "attachments" / "old.png").write_bytes(b"old")

    target.importer(replace=True)(archive)

    aside = target.prod.state / "replaced-20261106-093015"
    assert sorted(p.name for p in aside.iterdir()) == [
        "attachments",
        "state.db",
        "state.db-wal",
    ]
    assert pages(aside / "state.db") == {"mine": "keep"}
    assert (aside / "attachments" / "old.png").read_bytes() == b"old"
    assert pages(target.db) == {"style": "dry"}
    assert not (target.prod.state / "attachments" / "old.png").exists()
    assert any(str(aside) in line for line in target.lines)


def test_a_failed_migration_still_starts_the_services(archive, target):
    def fail(_):
        raise RuntimeError("rung broke")

    with pytest.raises(RuntimeError, match="rung broke"):
        target.importer(migrate=fail)(archive)

    assert target.sup.calls == STOP_START
    assert not target.staging_left()


def test_a_unit_that_does_not_start_fails_the_import(archive, target):
    class OrchestratorFails(FakeSupervisor):
        def apply(self, specs):
            super().apply(specs)
            return Applied(failed=["orchestrator"])

    target.sup = OrchestratorFails()

    with pytest.raises(ArchiveError, match="orchestrator did not start"):
        target.importer()(archive)

    assert pages(target.db) == {"style": "dry"}
