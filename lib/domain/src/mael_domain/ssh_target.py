"""The **SSH target** of a worktree: ``user@host:/path``.

A **Remote host** opens a terminal on the server's worktree with it. The parse
is strict because the remote host takes a target, never a command. See
"Remote clients" in ``docs/dev/cmux.md``.
"""

import re
import shlex
from dataclasses import dataclass

_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_HOST = re.compile(
    r"[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*"
)
#: No control character: a newline in the path would end the remote command.
_PATH = re.compile(r"/[^\x00-\x1f\x7f]*")


@dataclass(frozen=True)
class SshTarget:
    """One user, one host name, and one absolute path on that host."""

    user: str
    host: str
    path: str

    @staticmethod
    def parse(text: str) -> "SshTarget | None":
        """The target ``text`` names, or ``None`` when it is not ``user@host:/path``."""
        user, at, rest = text.partition("@")
        host, colon, path = rest.partition(":")
        if not at or not colon:
            return None
        if not (
            _USER.fullmatch(user) and _HOST.fullmatch(host) and _PATH.fullmatch(path)
        ):
            return None
        return SshTarget(user, host, path)

    def __str__(self) -> str:
        return f"{self.user}@{self.host}:{self.path}"

    def command(self) -> str:
        """The shell command that opens a login shell in ``path`` over SSH.

        Two shells read it: the local one reads the outer quotes, and the
        remote one reads the inner ``cd``. Each level is quoted for its shell.
        """
        remote = f'cd {shlex.quote(self.path)} && exec "$SHELL" -l'
        return f"ssh -t {shlex.quote(f'{self.user}@{self.host}')} {shlex.quote(remote)}"
