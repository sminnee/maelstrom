"""The files an agent named, and the ids that stand for them.

A URL carries an id and never a path: a file nobody registered is unreachable
because it is absent, not because a check caught it. Ids are guessable on
purpose — do not make them random and rely on that.

The registry is not persisted, exactly as a document is not.

See ``docs/dev/orchestrator-server.md``, "The file registry", for the design.
"""

from collections.abc import Callable
from pathlib import Path

from .document_tags import stays_within

#: Whether one resolved path names a file worth serving. Injected so a golden
#: does not depend on a directory this machine has, exactly as the normaliser
#: injects its reader.
IsFile = Callable[[Path], bool]


def _is_file(path: Path) -> bool:
    return path.is_file()


class FileRegistry:
    """Ids to the absolute paths they stand for."""

    def __init__(self, is_file: IsFile = _is_file) -> None:
        self._paths: dict[str, Path] = {}
        #: The id each resolved path already has. A replayed transcript
        #: registers its files again, so without this the registry would grow
        #: by one entry per replay and the same picture would change id.
        self._ids: dict[Path, str] = {}
        self._is_file = is_file

    def register(self, item_id: str, cwd: str, filename: str) -> str | None:
        """Register ``filename`` under ``cwd`` and return its id, or ``None``.

        ``None`` means the file is not one this agent may show: the path
        escapes the worktree, or names no file that is there. The caller says
        so to the user rather than minting a URL that cannot work.

        ``item_id`` makes the id unique. Two files may share a basename, so the
        name alone would collide.

        Registering one file twice returns the id it already has: the resolved
        path is the file's identity, whatever route named it.
        """
        if not stays_within(cwd, filename):
            return None
        try:
            resolved = (Path(cwd) / filename).resolve()
            # An id is minted for a file that is there. A missing path would
            # otherwise mint one whose fetch 404s, which is a broken picture —
            # the thing the refusal path exists to prevent.
            if not self._is_file(resolved):
                return None
        except OSError:
            # A worktree that is gone, or a path the host cannot walk. There is
            # no file to show and no id worth minting.
            return None
        known = self._ids.get(resolved)
        if known is not None:
            return known
        file_id = f"{item_id}-{Path(filename).name}"
        self._paths[file_id] = resolved
        self._ids[resolved] = file_id
        return file_id

    def resolve(self, file_id: str) -> Path | None:
        """The path ``file_id`` stands for, or ``None`` when it stands for none.

        An exact lookup. An id with anything appended is simply a different id,
        and reaches nothing.
        """
        return self._paths.get(file_id)
