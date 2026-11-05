"""Host services: the processes of the install that the OS supervisor runs.

See "Host service" in CONTEXT.md and ``docs/dev/host-services.md``.

Two host services run without end, the orchestrator and the agent daemon. The
scheduler is a third unit that the supervisor fires hourly. macOS runs each as a
launchd agent, and Linux as a systemd user unit. The unit files are rendered by
the pure functions :func:`render_plist` and :func:`render_unit`. A
:class:`Supervisor` writes them and drives ``launchctl`` or ``systemctl --user``
through an injected ``run``, so a test records the commands without running
them.
"""

import os
import platform
import plistlib
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

#: Runs one command and returns its result. It never raises on a non-zero exit.
Run = Callable[[list[str]], subprocess.CompletedProcess]

ORCHESTRATOR = "orchestrator"
AGENT_DAEMON = "agent-daemon"
SCHEDULER = "schedule"

#: The host services each changed component restarts. A component's SHA
#: bucket holds its ``depends``, so a change in ``domain`` also moves
#: ``orchestrator``: the map names only the direct owner. The orchestrator
#: serves ``web``.
RESTARTED_BY: Mapping[str, tuple[str, ...]] = {
    "orchestrator": (ORCHESTRATOR,),
    "web": (ORCHESTRATOR,),
    "agent-daemon": (AGENT_DAEMON,),
}

#: The launchd label prefix of the host services.
LABEL_PREFIX = "nz.tangerinelabs.maelstrom."
#: The scheduler keeps the label it had before host services existed, so an
#: upgrade replaces its old agent rather than running two.
SCHEDULER_LABEL = "nz.tangerinelabs.mael_domain.schedule"


@dataclass(frozen=True)
class HostService:
    """One supervised unit: what it runs, and how the supervisor runs it.

    ``hourly`` units run once an hour and exit, and the others run without
    end and restart when they die.
    """

    name: str
    argv: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict)
    hourly: bool = False
    #: The working directory. No ``.env`` is there to name another notebook.
    cwd: Path | None = None
    #: The log file, on macOS. Linux logs to the journal.
    log: Path | None = None

    @property
    def label(self) -> str:
        """The launchd label."""
        return SCHEDULER_LABEL if self.name == SCHEDULER else LABEL_PREFIX + self.name

    @property
    def unit(self) -> str:
        """The systemd unit name, without its suffix."""
        return f"maelstrom-{self.name}"


def render_plist(spec: HostService) -> bytes:
    """The launchd agent of ``spec``."""
    plist: dict[str, object] = {
        "Label": spec.label,
        "ProgramArguments": list(spec.argv),
        "EnvironmentVariables": dict(spec.env),
        "RunAtLoad": True,
    }
    if spec.hourly:
        # launchd fires a run missed during sleep on the next wake, once.
        plist["StartCalendarInterval"] = {"Minute": 0}
    else:
        plist["KeepAlive"] = True
    if spec.cwd is not None:
        plist["WorkingDirectory"] = str(spec.cwd)
    if spec.log is not None:
        plist["StandardOutPath"] = str(spec.log)
        plist["StandardErrorPath"] = str(spec.log)
    return plistlib.dumps(plist)


def render_unit(spec: HostService) -> dict[str, str]:
    """The systemd user unit files of ``spec``, by file name."""
    service = [
        "[Unit]",
        f"Description=maelstrom {spec.name}",
        "",
        "[Service]",
    ]
    if spec.hourly:
        service.append("Type=oneshot")
    service.append(f"ExecStart={' '.join(_quote(a) for a in spec.argv)}")
    service += [f"Environment={_quote(f'{k}={v}')}" for k, v in spec.env.items()]
    if spec.cwd is not None:
        service.append(f"WorkingDirectory={_quote(str(spec.cwd))}")
    if not spec.hourly:
        service += [
            "Restart=always",
            "RestartSec=5",
            "",
            "[Install]",
            "WantedBy=default.target",
        ]
    files = {f"{spec.unit}.service": "\n".join(service) + "\n"}
    if spec.hourly:
        files[f"{spec.unit}.timer"] = "\n".join(
            [
                "[Unit]",
                f"Description=maelstrom {spec.name}, hourly",
                "",
                "[Timer]",
                "OnCalendar=hourly",
                # A run missed while the host was off fires at the next boot.
                "Persistent=true",
                "",
                "[Install]",
                "WantedBy=timers.target",
                "",
            ]
        )
    return files


_PLAIN = re.compile(r"[A-Za-z0-9_@%+=:,./-]+")


def _quote(value: str) -> str:
    """One systemd word: double-quoted unless plain, and ``%`` escaped."""
    value = value.replace("%", "%%")
    if _PLAIN.fullmatch(value) and "%" not in value:
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass(frozen=True)
class ServiceStatus:
    """What the supervisor reports of one unit."""

    name: str
    loaded: bool
    pid: int | None = None
    last_exit: int | None = None


@dataclass
class Applied:
    """What a supervisor did: its messages, what it (re)started, and what failed."""

    messages: list[str] = field(default_factory=list)
    started: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def fail(self, name: str, message: str) -> None:
        self.failed.append(name)
        self.messages.append(f"{name}: {message}")


class Supervisor(Protocol):
    """The OS supervisor of the host services."""

    def apply(self, specs: Sequence[HostService]) -> Applied:
        """Write each unit, and load it. A loaded unit whose file changed restarts."""
        ...

    def remove(self, specs: Sequence[HostService]) -> list[str]:
        """Stop and unload each unit, and delete its files."""
        ...

    def restart(self, specs: Sequence[HostService]) -> Applied:
        """Restart each running unit, or start it."""
        ...

    def status(self, specs: Sequence[HostService]) -> list[ServiceStatus]:
        """Report each unit."""
        ...

    def log_hint(self, spec: HostService) -> str:
        """Where the log of ``spec`` is, for the user to read."""
        ...


def _write_if_changed(path: Path, content: bytes) -> bool:
    """Write ``content`` to ``path``; return whether the file changed."""
    if path.is_file() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    staging.write_bytes(content)
    os.replace(staging, path)
    return True


def _failed(result: subprocess.CompletedProcess) -> str:
    return (result.stderr or result.stdout or f"exit {result.returncode}").strip()


#: How long a booted-out launchd job may take to unload, in polls of
#: :data:`_POLL_SECS`. ``bootout`` returns before launchd has torn the job down.
_UNLOAD_POLLS = 40
_POLL_SECS = 0.25
#: A bootstrap that races the teardown fails with ``5: Input/output error``.
_BOOTSTRAP_TRIES = 3


@dataclass
class Launchd(Supervisor):
    """launchd agents in ``~/Library/LaunchAgents``, in the user's GUI domain.

    The GUI domain exists only for a user logged in at the console, so the
    agents start at login, not at boot.
    """

    run: Run
    home: Path
    uid: int = field(default_factory=os.getuid)
    sleep: Callable[[float], None] = time.sleep

    def _plist(self, spec: HostService) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{spec.label}.plist"

    def _target(self, spec: HostService) -> str:
        return f"gui/{self.uid}/{spec.label}"

    def _loaded(self, spec: HostService) -> bool:
        return self.run(["launchctl", "print", self._target(spec)]).returncode == 0

    def _unload(self, spec: HostService) -> None:
        """Boot the job out, and wait until launchd has unloaded it."""
        self.run(["launchctl", "bootout", self._target(spec)])
        for _ in range(_UNLOAD_POLLS):
            if not self._loaded(spec):
                return
            self.sleep(_POLL_SECS)

    def _load(self, spec: HostService, applied: Applied) -> None:
        """Bootstrap the job, retrying the race with a teardown in flight."""
        plist = self._plist(spec)
        for attempt in range(_BOOTSTRAP_TRIES):
            result = self.run(["launchctl", "bootstrap", f"gui/{self.uid}", str(plist)])
            if result.returncode == 0 or "already" in _failed(result).lower():
                applied.started.append(spec.name)
                applied.messages.append(f"{spec.name}: loaded ({plist})")
                return
            if attempt + 1 < _BOOTSTRAP_TRIES:
                self.sleep(1)
        applied.fail(spec.name, f"launchctl bootstrap failed: {_failed(result)}")

    def apply(self, specs: Sequence[HostService]) -> Applied:
        applied = Applied()
        for spec in specs:
            if spec.log is not None:
                spec.log.parent.mkdir(parents=True, exist_ok=True)
            changed = _write_if_changed(self._plist(spec), render_plist(spec))
            loaded = self._loaded(spec)
            if loaded and not changed:
                continue
            if loaded:
                # A changed plist takes effect only on a fresh load.
                self._unload(spec)
            self._load(spec, applied)
        return applied

    def remove(self, specs: Sequence[HostService]) -> list[str]:
        messages = []
        for spec in specs:
            plist = self._plist(spec)
            self.run(["launchctl", "bootout", self._target(spec)])
            if plist.exists():
                plist.unlink()
                messages.append(f"{spec.name}: removed ({plist})")
        return messages

    def restart(self, specs: Sequence[HostService]) -> Applied:
        applied = Applied()
        for spec in specs:
            if not self._loaded(spec) and self._plist(spec).exists():
                # kickstart knows only a loaded job.
                self._load(spec, applied)
                continue
            result = self.run(["launchctl", "kickstart", "-k", self._target(spec)])
            if result.returncode == 0:
                applied.messages.append(f"{spec.name}: restarted")
            else:
                applied.fail(spec.name, f"restart failed: {_failed(result)}")
        return applied

    def status(self, specs: Sequence[HostService]) -> list[ServiceStatus]:
        statuses = []
        for spec in specs:
            result = self.run(["launchctl", "print", self._target(spec)])
            if result.returncode != 0:
                statuses.append(ServiceStatus(spec.name, loaded=False))
                continue
            statuses.append(
                ServiceStatus(
                    spec.name,
                    loaded=True,
                    pid=_field(result.stdout, r"^\s*pid = (\d+)"),
                    last_exit=_field(result.stdout, r"^\s*last exit code = (-?\d+)"),
                )
            )
        return statuses

    def log_hint(self, spec: HostService) -> str:
        return str(spec.log) if spec.log is not None else "(no log)"


def _field(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text, re.MULTILINE)
    return int(match.group(1)) if match else None


@dataclass
class Systemd(Supervisor):
    """systemd user units in ``~/.config/systemd/user``, kept up by linger."""

    run: Run
    home: Path
    user: str = field(default_factory=lambda: os.environ.get("USER", ""))

    @property
    def _dir(self) -> Path:
        return self.home / ".config" / "systemd" / "user"

    def _systemctl(self, *args: str) -> subprocess.CompletedProcess:
        return self.run(["systemctl", "--user", *args])

    def _target(self, spec: HostService) -> str:
        """The unit that is enabled: the timer of an hourly unit."""
        return f"{spec.unit}.timer" if spec.hourly else f"{spec.unit}.service"

    def apply(self, specs: Sequence[HostService]) -> Applied:
        applied = Applied()
        # Without linger, the user's units stop at logout and wait for login.
        linger = self.run(["loginctl", "enable-linger", self.user])
        if linger.returncode != 0:
            applied.messages.append(
                f"Warning: loginctl enable-linger failed: {_failed(linger)}. "
                "The host services stop when you log out."
            )
        changed = {
            spec.name: any(
                [
                    _write_if_changed(self._dir / name, text.encode())
                    for name, text in render_unit(spec).items()
                ]
            )
            for spec in specs
        }
        self._systemctl("daemon-reload")
        for spec in specs:
            target = self._target(spec)
            if changed[spec.name] and not spec.hourly:
                self._systemctl("enable", target)
                result = self._systemctl("restart", target)
            else:
                result = self._systemctl("enable", "--now", target)
            if result.returncode != 0:
                applied.fail(spec.name, f"systemctl failed: {_failed(result)}")
                continue
            if changed[spec.name]:
                applied.started.append(spec.name)
                applied.messages.append(f"{spec.name}: loaded ({self._dir / target})")
        return applied

    def remove(self, specs: Sequence[HostService]) -> list[str]:
        messages = []
        for spec in specs:
            self._systemctl("disable", "--now", self._target(spec))
            for name in render_unit(spec):
                path = self._dir / name
                if path.exists():
                    path.unlink()
                    messages.append(f"{spec.name}: removed ({path})")
        self._systemctl("daemon-reload")
        return messages

    def restart(self, specs: Sequence[HostService]) -> Applied:
        applied = Applied()
        for spec in specs:
            result = self._systemctl("restart", f"{spec.unit}.service")
            if result.returncode == 0:
                applied.messages.append(f"{spec.name}: restarted")
            else:
                applied.fail(spec.name, f"restart failed: {_failed(result)}")
        return applied

    def status(self, specs: Sequence[HostService]) -> list[ServiceStatus]:
        statuses = []
        for spec in specs:
            result = self._systemctl(
                "show",
                self._target(spec),
                "--property=LoadState,ActiveState,MainPID,ExecMainStatus",
            )
            props = dict(
                line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
            )
            pid = int(props.get("MainPID", "0") or 0)
            statuses.append(
                ServiceStatus(
                    spec.name,
                    loaded=props.get("LoadState") == "loaded",
                    pid=pid or None,
                    last_exit=int(props["ExecMainStatus"])
                    if props.get("ExecMainStatus", "").lstrip("-").isdigit()
                    else None,
                )
            )
        return statuses

    def log_hint(self, spec: HostService) -> str:
        return f"journalctl --user -u {spec.unit}.service"


def supervisor(run: Run, home: Path) -> Supervisor:
    """The supervisor of this OS: launchd on macOS, else systemd."""
    if platform.system() == "Darwin":
        return Launchd(run=run, home=home)
    return Systemd(run=run, home=home)


def run_quietly(argv: list[str]) -> subprocess.CompletedProcess:
    """The real :data:`Run`: capture the output, and never raise on exit."""
    try:
        return subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))
