"""The attachments-move ladder: move the media out of the old git notebook.

Media sat at ``<root>/tasks/<project>/images/<bucket>/``. They now sit in the
attachments directory, ``<root>/attachments/<project>/images/<bucket>/``.

A token ref, ``{{MAEL_TASK_DIR}}/images/…``, needs no rewrite: the token now
expands under the attachments directory. An absolute ref under the old root
does. The rung rewrites it to the token form when it names the row's own
project, so it survives a later move to a host with a different home
directory. A ref to another project's media gets the new absolute path.

Stdlib only, because a rung sits at the bottom layer.
"""

import logging
import re
import shutil
import sqlite3
from pathlib import Path

from ..paths import attachments_path, notebook_path
from ..types import PythonMigration, Rung

log = logging.getLogger(__name__)

#: Duplicated from :data:`mael_domain.task.MAEL_TASK_DIR_TOKEN`, and pinned by a
#: test that asserts the two agree.
_TOKEN = "{{MAEL_TASK_DIR}}"


def _move_media(root: Path) -> None:
    """Move each bucket to the attachments directory.

    A bucket whose target already exists is kept, and its source is left where
    it is, so a second run loses nothing.
    """
    for images in sorted(notebook_path(root).glob("*/images")):
        project = images.parent.name
        if project.startswith((".", "_")) or not images.is_dir():
            continue
        for bucket in sorted(images.iterdir()):
            if not bucket.is_dir():
                continue
            target = attachments_path(root) / project / "images" / bucket.name
            if target.exists():
                log.warning("%s exists: %s left in place", target, bucket)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(bucket, target)


def _rewriter(root: Path):
    """A function that rewrites old absolute refs in one row's text."""
    pattern = re.compile(re.escape(str(notebook_path(root))) + r"/([^/\s()]+)/images/")
    new_root = str(attachments_path(root))

    def rewrite(text: str, project: str) -> str:
        def one(match: re.Match[str]) -> str:
            if match[1] == project:
                return f"{_TOKEN}/images/"
            return f"{new_root}/{match[1]}/images/"

        return pattern.sub(one, text)

    return rewrite


def _rewrite_refs(conn: sqlite3.Connection, root: Path) -> None:
    """Rewrite old absolute refs in task content and attached documents."""
    rewrite = _rewriter(root)
    like = f"%{notebook_path(root)}/%/images/%"
    for id, project, content in conn.execute(
        "SELECT id, project, content FROM tasks WHERE content LIKE ?", (like,)
    ).fetchall():
        conn.execute(
            "UPDATE tasks SET content = ? WHERE id = ?",
            (rewrite(content, project), id),
        )
    # The `task_attachments` ladder climbs first, so a Task document's text is
    # in its versions by now.
    for id, key, body in conn.execute(
        "SELECT v.id, a.task_key, v.body FROM task_attachment_versions v "
        "JOIN task_attachments a ON a.id = v.attachment_id WHERE v.body LIKE ?",
        (like,),
    ).fetchall():
        conn.execute(
            "UPDATE task_attachment_versions SET body = ? WHERE id = ?",
            (rewrite(body, key.partition("/")[0]), id),
        )


def _move_attachments(conn: sqlite3.Connection, root: Path | None) -> None:
    """Move the media, then rewrite the refs to them.

    The rows are rewritten inside the migration's transaction. The file move
    is not, so it runs first: a failed move leaves the rows as they were.

    With no old notebook there is nothing to move, and an old ref names a file
    that is gone, so the rows are left as they are.
    """
    if root is None or not notebook_path(root).is_dir():
        return
    _move_media(root)
    _rewrite_refs(conn, root)


#: The attachments-move ladder. Append-only, so a rung's version is its index
#: plus one. It creates no table: the ladder version only records the move.
ATTACHMENTS_MOVE: tuple[Rung, ...] = (
    PythonMigration(
        run=_move_attachments,
        description="move the media out of the old git notebook",
    ),
)
