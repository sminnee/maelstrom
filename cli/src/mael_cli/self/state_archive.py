"""``mael self export`` and ``mael self import``: the State archive.

See ``docs/dev/host-services.md``.
"""

import asyncio
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath

from mael_domain.state_db.migrate import LADDERS, TABLES, migrate_state_db
from mael_domain.state_db.migrations.spine import SPINE
from mael_domain.state_db.paths import attachments_path

from .host_service import HostService, Supervisor
from .production import Production
from .setup import Echo

#: The archive format this build writes and reads.
FORMAT = 1

MANIFEST = "manifest.json"
DB = "state.db"
ATTACHMENTS = "attachments"

#: The files beside a database that belong to it. The WAL files go first, so
#: a database never sits without its uncheckpointed log.
_DB_FILES = ("state.db-wal", "state.db-shm", DB)

#: The tables a fresh database leaves empty and only a user's work fills. A
#: cached table refills itself, so a row in one is not state to keep.
_USER_TABLES = tuple(name for name, spec in TABLES.items() if not spec.cached)


class ArchiveError(RuntimeError):
    """A State archive cannot be written or read."""


@dataclass(frozen=True)
class Manifest:
    """What a State archive says about itself."""

    maelstrom: str
    commit: str | None
    spine: int
    ladders: dict[str, int]
    created: str
    host: str
    format: int = FORMAT

    def to_json(self) -> str:
        return json.dumps(
            {
                "format": self.format,
                "maelstrom": self.maelstrom,
                "commit": self.commit,
                "spine": self.spine,
                "ladders": self.ladders,
                "created": self.created,
                "host": self.host,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str | bytes) -> "Manifest":
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ArchiveError(f"{MANIFEST} is not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ArchiveError(f"{MANIFEST} is not a JSON object.")
        fmt = data.get("format")
        if not isinstance(fmt, int):
            raise ArchiveError(f"{MANIFEST} has no format version.")
        if fmt > FORMAT:
            raise ArchiveError(
                f"The archive is in format {fmt}, and this build reads format "
                f"{FORMAT}. Update maelstrom, then import again."
            )
        ladders = data.get("ladders")
        spine = data.get("spine")
        if (
            not isinstance(spine, int)
            or not isinstance(ladders, dict)
            or not all(
                isinstance(k, str) and isinstance(v, int) for k, v in ladders.items()
            )
        ):
            raise ArchiveError(f"{MANIFEST} has no valid schema versions.")
        commit = data.get("commit")
        return cls(
            format=fmt,
            maelstrom=str(data.get("maelstrom", "")),
            commit=commit if isinstance(commit, str) else None,
            spine=spine,
            ladders=ladders,
            created=str(data.get("created", "")),
            host=str(data.get("host", "")),
        )


def default_name(host: str, today: date) -> str:
    """The file name ``mael self export`` writes without a path."""
    return f"maelstrom-{host}-{today.isoformat()}.zip"


def _versions(conn: sqlite3.Connection) -> tuple[int, dict[str, int]]:
    spine = conn.execute("PRAGMA user_version").fetchone()[0]
    if spine == 0:
        return 0, {}
    rows = conn.execute("SELECT name, version FROM schema_version ORDER BY name")
    return spine, dict(rows.fetchall())


def _attachment_files(root: Path) -> list[Path]:
    """Every regular file under ``root``. A symlink is skipped."""
    files = []
    for dirpath, _, names in sorted(os.walk(root, followlinks=False)):
        for name in sorted(names):
            path = Path(dirpath) / name
            if path.is_file() and not path.is_symlink():
                files.append(path)
    return files


def export_state(
    root: Path,
    dest: Path,
    *,
    host: str,
    now: datetime,
    maelstrom: str,
    commit: str | None,
) -> Manifest:
    """Write the State archive of the notebook root ``root`` to ``dest``.

    Raises:
        ArchiveError: If ``root`` has no state database or ``dest`` exists.
    """
    src_path = root / DB
    if not src_path.is_file():
        raise ArchiveError(f"{src_path} does not exist.")
    if dest.exists():
        raise ArchiveError(f"{dest} exists. Give another path.")
    tmp = dest.with_name(dest.name + ".tmp")
    with tempfile.TemporaryDirectory() as scratch:
        snapshot = Path(scratch) / DB
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(snapshot)
        try:
            src.backup(dst)
            spine, ladders = _versions(dst)
        finally:
            dst.close()
            src.close()
        manifest = Manifest(
            maelstrom=maelstrom,
            commit=commit,
            spine=spine,
            ladders=ladders,
            created=now.astimezone(UTC).isoformat(timespec="seconds"),
            host=host,
        )
        attachments = attachments_path(root)
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(MANIFEST, manifest.to_json())
                zf.write(snapshot, DB)
                for path in _attachment_files(attachments):
                    rel = path.relative_to(attachments).as_posix()
                    zf.write(path, f"{ATTACHMENTS}/{rel}")
            os.replace(tmp, dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    return manifest


def _too_new(what: str, name: str, found: int, build: int) -> str:
    return (
        f"{what}'s {name} schema is at version {found}, and this build "
        f"knows version {build}. Update maelstrom, then import again."
    )


def _check_entry(info: zipfile.ZipInfo) -> None:
    name = info.filename
    path = PurePosixPath(name)
    if not path.parts or name.startswith("/") or ".." in path.parts:
        raise ArchiveError(f"The archive holds an unsafe entry: {name!r}.")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ArchiveError(f"The archive holds a symlink: {name!r}.")
    if name in (MANIFEST, DB):
        return
    if path.parts[0] == ATTACHMENTS and (len(path.parts) > 1 or info.is_dir()):
        return
    raise ArchiveError(f"The archive holds an unknown entry: {name!r}.")


def _check_versions(spine: int, ladders: dict[str, int], what: str) -> None:
    """Refuse versions this build cannot migrate."""
    if spine > len(SPINE):
        raise ArchiveError(_too_new(what, "spine", spine, len(SPINE)))
    for name, version in ladders.items():
        ladder = LADDERS.get(name)
        if ladder is None:
            if version > 0:
                raise ArchiveError(
                    f"{what} has a {name} schema, and this build does not know "
                    "it. Update maelstrom, then import again."
                )
        elif version > len(ladder):
            raise ArchiveError(_too_new(what, name, version, len(ladder)))


def check_database(db: Path) -> None:
    """Refuse a staged database that is not SQLite or is too new. Reads only."""
    try:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ArchiveError(f"The archive's {DB} is damaged.")
            spine, ladders = _versions(conn)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise ArchiveError(f"The archive's {DB} is not a database: {exc}") from exc
    _check_versions(spine, ladders, f"The archive's {DB}")


def check_archive(zf: zipfile.ZipFile) -> Manifest:
    """Read and check the archive's manifest and entries. Changes nothing.

    Raises:
        ArchiveError: If this build cannot import the archive.
    """
    names = zf.namelist()
    if MANIFEST not in names:
        raise ArchiveError(f"The archive has no {MANIFEST}.")
    manifest = Manifest.from_json(zf.read(MANIFEST))
    _check_versions(manifest.spine, manifest.ladders, "The archive")
    for info in zf.infolist():
        _check_entry(info)
    if DB not in names:
        raise ArchiveError(f"The archive has no {DB}.")
    return manifest


def holds_rows(db: Path) -> bool:
    """Whether the database at ``db`` holds a user row. A missing table is empty."""
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        return any(
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
            for table in _USER_TABLES
            if table in tables
        )
    finally:
        conn.close()


def _holds_state(root: Path) -> bool:
    """Whether ``root`` holds state that import must not delete."""
    db = root / DB
    if db.exists() and holds_rows(db):
        return True
    attachments = attachments_path(root)
    return attachments.is_dir() and any(attachments.iterdir())


def _migrate(path: Path) -> None:
    asyncio.run(migrate_state_db(path))


@dataclass
class Import:
    """One import of a State archive, with every outside effect injected."""

    prod: Production
    supervisor: Supervisor
    units: Sequence[HostService]
    echo: Echo
    replace: bool = False
    now: datetime = field(default_factory=datetime.now)
    migrate: Callable[[Path], None] = _migrate

    def __call__(self, archive: Path) -> None:
        """Import ``archive`` into ``Production.state``.

        Every check of the archive comes before a change to the root. The
        root is checked again after the stop, because a unit can write before
        it stops. The host services start again after a stop, also when the
        swap or the migration fails.

        Raises:
            ArchiveError: If the archive cannot be imported.
            StateDbError: If the migration refuses the imported database.
        """
        root = self.prod.state
        try:
            zf = zipfile.ZipFile(archive)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ArchiveError(f"{archive} is not a zip: {exc}") from exc
        with zf:
            manifest = check_archive(zf)
            self._refuse_existing(root)
            self.echo(
                f"Importing the state of {manifest.host or 'an unknown host'} "
                f"from {manifest.created or 'an unknown date'}..."
            )
            root.mkdir(parents=True, exist_ok=True)
            staging = root / f".import-{os.getpid()}"
            shutil.rmtree(staging, ignore_errors=True)
            try:
                zf.extractall(staging)
                check_database(staging / DB)
                self._swap_and_migrate(root, staging)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        self.echo(f"Imported the state into {root}.")

    def _refuse_existing(self, root: Path) -> None:
        if not self.replace and _holds_state(root):
            raise ArchiveError(
                f"{root} already holds state. Give --replace to keep it aside "
                "and import the archive."
            )

    def _swap_and_migrate(self, root: Path, staging: Path) -> None:
        self.echo("Stopping the host services...")
        for msg in self.supervisor.remove(self.units):
            self.echo(f"  {msg}")
        failed: list[str] = []
        try:
            # The services may have written since the first check.
            self._refuse_existing(root)
            self._clear(root)
            (staging / DB).rename(root / DB)
            if (staging / ATTACHMENTS).is_dir():
                (staging / ATTACHMENTS).rename(attachments_path(root))
            self.echo("Migrating the state database...")
            self.migrate(root / DB)
        finally:
            self.echo("Starting the host services...")
            applied = self.supervisor.apply(self.units)
            for msg in applied.messages:
                self.echo(f"  {msg}")
            failed = applied.failed
        if failed:
            raise ArchiveError(
                f"The state is imported, and {', '.join(failed)} did not start. "
                "Run `mael self setup`."
            )

    def _clear(self, root: Path) -> None:
        """Move the old state aside, or delete it when it holds nothing."""
        old = [root / name for name in (*_DB_FILES, ATTACHMENTS)]
        present = [path for path in old if path.exists()]
        if not present:
            return
        if not _holds_state(root):
            for path in present:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            return
        aside = root / f"replaced-{self.now:%Y%m%d-%H%M%S}"
        aside.mkdir()
        for path in present:
            path.rename(aside / path.name)
        self.echo(f"  Kept the old state in {aside}. Delete it when you are done.")
