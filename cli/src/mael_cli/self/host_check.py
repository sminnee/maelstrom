"""``mael self check-host``: take a fresh host to one maelstrom can run on.

The check works through :data:`HOST_ITEMS`. See ``docs/dev/host-services.md``.
"""

import getpass
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from mael_agent.agent_transport import DaemonPaths
from mael_common.util import harden_path
from mael_domain.context import read_config_key, set_config_key
from mael_domain.dev_cert import DevCertError, ensure_dev_cert
from mael_domain.ports import ALLOCATIONS_FILENAME

from .host_service import Run
from .production import Production
from .setup import ORCHESTRATOR_URL_KEY, Echo, tailnet_name

HOMEBREW_INSTALL = (
    '/bin/bash -c "$(curl -fsSL '
    'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
)

_GH_APT = (
    "sudo mkdir -p -m 755 /etc/apt/keyrings"
    " && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg"
    " | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null"
    " && sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg"
    ' && echo "deb [arch=$(dpkg --print-architecture)'
    " signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg]"
    ' https://cli.github.com/packages stable main"'
    " | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null"
    " && sudo apt-get update && sudo apt-get install -y gh"
)

_NODESOURCE = (
    "curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -"
    " && sudo apt-get install -y nodejs"
)


@dataclass(frozen=True)
class Finding:
    """What an item found: met or not, and the line that says so."""

    met: bool
    text: str


@dataclass(frozen=True)
class Remedy:
    """How to meet an unmet item.

    ``hint`` is printed. With ``fix``, the check runs it: after a yes to
    ``question``, or at once when ``question`` is ``None``. Then it checks the
    item again.
    """

    hint: str
    fix: Callable[[], object] | None = None
    question: str | None = None


class HostCheckItem:
    """One thing a host needs. Subclasses check it and name its remedy."""

    #: The heading the item prints under.
    section: ClassVar[str] = ""
    #: An unmet required item makes the check fail.
    required: bool = False
    #: The name an unmet required item is reported by.
    name: str

    def applies(self, host: "HostCheck") -> bool:
        return True

    def check(self, host: "HostCheck") -> Finding:
        raise NotImplementedError

    def remedy(self, host: "HostCheck") -> Remedy | None:
        return None


class PackageManager(HostCheckItem):
    """``brew`` on macOS, ``apt-get`` on Linux: the tool commands use them."""

    section = "Package manager"

    def check(self, host: "HostCheck") -> Finding:
        name = "brew" if host.platform == "darwin" else "apt-get"
        if host.which(name):
            return Finding(True, name)
        if name == "brew":
            return Finding(False, "Homebrew is missing")
        return Finding(False, "apt-get is missing. The Linux commands below use it.")

    def remedy(self, host: "HostCheck") -> Remedy | None:
        if host.platform != "darwin":
            return None
        return Remedy(
            f"Install it from https://brew.sh:\n      {HOMEBREW_INSTALL}",
            fix=lambda: host.run_interactive(["sh", "-c", HOMEBREW_INSTALL]),
            question="Install Homebrew?",
        )


@dataclass(frozen=True)
class Tool(HostCheckItem):
    """A command the agents or the install use, and how each platform installs it."""

    section: ClassVar[str] = "Tools"

    name: str
    darwin: str | None
    linux: str
    required: bool = False
    #: Another tool the install command runs, such as ``node`` for npm.
    needs: str | None = None
    #: On the list only for a source install (``--dev``).
    dev: bool = False

    def command(self, platform: str) -> str | None:
        return self.darwin if platform == "darwin" else self.linux

    def applies(self, host: "HostCheck") -> bool:
        return host.dev or not self.dev

    def check(self, host: "HostCheck") -> Finding:
        if host.which(self.name):
            return Finding(True, self.name)
        kind = "required" if self.required else "optional"
        return Finding(False, f"{self.name} ({kind}) is missing")

    def remedy(self, host: "HostCheck") -> Remedy | None:
        command = self.command(host.platform)
        if command is None:
            return Remedy("It ships with macOS.")
        hint = f"Install it with:\n      {command}"
        if host.lacks_brew(command):
            return Remedy(f"{hint}\n    That needs Homebrew, above.")
        needed = _TOOLS[self.needs] if self.needs else None
        if needed is None or host.which(needed.name):
            return Remedy(
                hint,
                fix=lambda: host.run_interactive(["sh", "-c", command]),
                question=f"Install {self.name}?",
            )
        needed_command = needed.command(host.platform)
        if needed_command is None or host.lacks_brew(needed_command):
            return Remedy(
                f"{hint}\n    That needs {needed.name}, which is missing too."
            )

        def fix() -> None:
            host.run_interactive(["sh", "-c", needed_command])
            if host.which(needed.name):
                host.run_interactive(["sh", "-c", command])

        return Remedy(
            f"{hint}\n    That needs {needed.name}, which is missing too:\n"
            f"      {needed_command}",
            fix=fix,
            question=f"Install {needed.name}, then {self.name}?",
        )


class SecretFiles(HostCheckItem):
    """The files under ``~/.maelstrom`` that hold secrets are private.

    The config holds API keys, and each spawn record holds the env its agent was
    started with. The fix only narrows a mode, so it runs without a question.
    """

    section = "Secret files"

    def targets(self, host: "HostCheck") -> list[tuple[Path, int]]:
        mael = host.prod.maelstrom
        targets = [
            (mael, 0o700),
            (host.prod.config, 0o600),
            (mael / ALLOCATIONS_FILENAME, 0o600),
        ]
        for agents in (mael / "agents", DaemonPaths(host.prod.daemon_root).spec_dir):
            targets.append((agents, 0o700))
            targets += [(r, 0o600) for r in sorted(agents.glob("*.json"))]
        return [(p, m) for p, m in targets if p.exists()]

    def loose(self, host: "HostCheck") -> list[tuple[Path, int]]:
        return [(p, m) for p, m in self.targets(host) if p.stat().st_mode & ~m & 0o777]

    def check(self, host: "HostCheck") -> Finding:
        loose = self.loose(host)
        if not loose:
            return Finding(True, "only you can read them")
        labels = ", ".join(host.label(p) for p, _ in loose)
        return Finding(False, f"others can read {labels}")

    def remedy(self, host: "HostCheck") -> Remedy | None:
        def fix() -> None:
            for path, mode in self.loose(host):
                try:
                    harden_path(path, mode)
                except OSError:
                    continue
                host.echo(f"  ✓ tightened {host.label(path)}")

        return Remedy("Tightening them.", fix=fix)


@dataclass(frozen=True)
class SignIn(HostCheckItem):
    """A tool the agents use is signed in. Checked only when it is installed."""

    section: ClassVar[str] = "Sign-ins"

    tool: str
    status: tuple[str, ...]
    darwin_login: str
    linux_login: str
    required: bool = False

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"{self.tool} sign-in"

    def applies(self, host: "HostCheck") -> bool:
        return host.which(self.tool) is not None

    def check(self, host: "HostCheck") -> Finding:
        if signed_in(host.run(list(self.status))):
            return Finding(True, f"{self.tool} is signed in")
        return Finding(False, f"{self.tool} is signed out")

    def remedy(self, host: "HostCheck") -> Remedy | None:
        login = self.darwin_login if host.platform == "darwin" else self.linux_login
        return Remedy(
            f"Sign in with: {login}",
            fix=lambda: host.run_interactive(login.split()),
            question=f"Sign in to {self.tool} now?",
        )


def signed_in(result) -> bool:
    """Whether a status command says signed in: exit 0, and no ``loggedIn: false``."""
    if result.returncode != 0:
        return False
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return True
    return not isinstance(data, dict) or data.get("loggedIn") is not False


class TailscaleUp(HostCheckItem):
    """This machine is on a tailnet. Checked only when tailscale is installed."""

    section = "Tailscale"

    def applies(self, host: "HostCheck") -> bool:
        return host.which("tailscale") is not None

    def check(self, host: "HostCheck") -> Finding:
        if host.tailscale_running():
            return Finding(True, "connected")
        return Finding(False, "not connected to a tailnet")

    def remedy(self, host: "HostCheck") -> Remedy | None:
        command = [*host.sudo, "tailscale", "up"]
        return Remedy(
            f"Connect with: {' '.join(command)}",
            fix=lambda: host.run_interactive(command),
            question="Connect this machine to your tailnet?",
        )


class TailscaleOperator(HostCheckItem):
    """On Linux, the user runs ``tailscale cert`` without sudo, as the dev env does."""

    section = "Tailscale"

    def applies(self, host: "HostCheck") -> bool:
        return host.platform == "linux" and host.which("tailscale") is not None

    def check(self, host: "HostCheck") -> Finding:
        result = host.run(["tailscale", "debug", "prefs"])
        try:
            current = json.loads(result.stdout).get("OperatorUser")
        except (ValueError, AttributeError):
            current = None
        if current == host.user:
            return Finding(True, f"{host.user} is the tailscale operator")
        return Finding(
            False,
            f"{host.user} is not the tailscale operator, so `tailscale cert` needs sudo",
        )

    def remedy(self, host: "HostCheck") -> Remedy | None:
        command = [*host.sudo, "tailscale", "set", f"--operator={host.user}"]
        return Remedy(
            f"Fix it with: {' '.join(command)}",
            fix=lambda: host.run_interactive(command),
            question=f"Make {host.user} the tailscale operator?",
        )


class DevHost(HostCheckItem):
    """``dev_host:`` names this machine. Checked only on a running tailnet."""

    section = "Tailscale"

    def applies(self, host: "HostCheck") -> bool:
        return host.which("tailscale") is not None and host.tailscale_running()

    def check(self, host: "HostCheck") -> Finding:
        value = read_config_key(host.prod.config, "dev_host")
        if value:
            return Finding(True, f"dev_host: {value}")
        return Finding(False, "dev_host is unset")

    def remedy(self, host: "HostCheck") -> Remedy | None:
        name = tailnet_name(host.run)
        if name is None:
            return None
        config = host.prod.config

        def fix() -> None:
            set_config_key(config, "dev_host", name)
            if read_config_key(config, ORCHESTRATOR_URL_KEY):
                host.echo(
                    f"    {ORCHESTRATOR_URL_KEY} in {config} keeps its host. Edit "
                    "it to use the tailnet name."
                )

        return Remedy(
            f"This machine's tailnet name is {name}.",
            fix=fix,
            question=f"Set dev_host to {name}?",
        )


class DevHttps(HostCheckItem):
    """``dev_https:`` is set. Turning it on issues the tailnet certificate first."""

    section = "Tailscale"

    def applies(self, host: "HostCheck") -> bool:
        return DevHost().applies(host) and bool(
            read_config_key(host.prod.config, "dev_host")
        )

    def check(self, host: "HostCheck") -> Finding:
        if read_config_key(host.prod.config, "dev_https") is not None:
            return Finding(True, "dev_https is set")
        return Finding(False, "dev_https is unset")

    def remedy(self, host: "HostCheck") -> Remedy | None:
        dev_host = str(read_config_key(host.prod.config, "dev_host"))

        def fix() -> None:
            try:
                host.issue_cert(dev_host)
            except DevCertError as exc:
                host.echo(f"  ✗ {exc}")
                return
            set_config_key(host.prod.config, "dev_https", "true")

        return Remedy(
            f"A tailnet certificate for {dev_host} turns it on.",
            fix=fix,
            question=f"Issue a certificate for {dev_host}?",
        )


class ShimOnPath(HostCheckItem):
    """The shim's directory is on the user's PATH, so ``mael`` is found."""

    section = "PATH"

    def check(self, host: "HostCheck") -> Finding:
        shim_dir = host.prod.shim.parent
        entries = [Path(p) for p in host.caller_path.split(os.pathsep) if p]
        if shim_dir in entries:
            return Finding(True, f"{shim_dir} is on PATH")
        return Finding(False, f"{shim_dir} is not on your PATH, so `mael` is not found")

    def remedy(self, host: "HostCheck") -> Remedy | None:
        return Remedy(
            "Add this line to your shell profile:\n"
            '      export PATH="$HOME/.local/bin:$PATH"'
        )


#: In install order: a tool comes after the tool its command needs.
HOST_TOOLS = (
    Tool("git", "xcode-select --install", "sudo apt-get install -y git", required=True),
    Tool("gh", "brew install gh", _GH_APT, required=True),
    Tool(
        "uv",
        "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "curl -LsSf https://astral.sh/uv/install.sh | sh",
        required=True,
    ),
    Tool(
        "claude",
        "curl -fsSL https://claude.ai/install.sh | bash",
        "curl -fsSL https://claude.ai/install.sh | bash",
        required=True,
    ),
    Tool("lsof", None, "sudo apt-get install -y lsof", required=True),
    Tool("node", "brew install node", _NODESOURCE, dev=True),
    Tool(
        "pnpm", "brew install pnpm", "sudo npm install -g pnpm", needs="node", dev=True
    ),
    Tool(
        "codex",
        "npm install -g @openai/codex",
        "sudo npm install -g @openai/codex",
        needs="node",
    ),
    Tool(
        "tailscale",
        "brew install --cask tailscale-app",
        "curl -fsSL https://tailscale.com/install.sh | sh",
    ),
    Tool("tangier", "uv tool install tangier", "uv tool install tangier"),
)

_TOOLS = {t.name: t for t in HOST_TOOLS}

#: Everything a host needs, in the order the check works through it.
HOST_ITEMS: tuple[HostCheckItem, ...] = (
    PackageManager(),
    *HOST_TOOLS,
    SecretFiles(),
    SignIn(
        "gh", ("gh", "auth", "status"), "gh auth login", "gh auth login", required=True
    ),
    SignIn(
        "claude",
        ("claude", "auth", "status"),
        "claude auth login",
        "claude auth login",
        required=True,
    ),
    SignIn(
        "codex",
        ("codex", "login", "status"),
        "codex login",
        "codex login --device-auth",
    ),
    TailscaleUp(),
    TailscaleOperator(),
    DevHost(),
    DevHttps(),
    ShimOnPath(),
)


@dataclass
class HostCheck:
    """One host check: the effects the items use, and the loop over them."""

    prod: Production
    platform: str
    #: Whether a user can answer. Without one, the check only reports.
    interactive: bool
    #: Finds a tool on the host services' PATH.
    which: Callable[[str], str | None]
    run: Run
    #: Runs a command with the user's terminal, and returns its exit code.
    run_interactive: Callable[[Sequence[str]], int]
    confirm: Callable[[str], bool]
    echo: Echo
    #: Adds the tools a source install needs.
    dev: bool = False
    #: The caller's PATH, to tell whether the shim's directory is on it.
    caller_path: str = field(default_factory=lambda: os.environ.get("PATH", ""))
    user: str = field(default_factory=getpass.getuser)
    #: Whether the check runs as root, which needs no sudo.
    root: bool = field(default_factory=lambda: os.geteuid() == 0)
    issue_cert: Callable[[str], object] = ensure_dev_cert
    items: Sequence[HostCheckItem] = HOST_ITEMS

    def __call__(self) -> list[str]:
        """Work through the items. Returns the required items still unmet."""
        unmet: list[str] = []
        section = None
        for item in self.items:
            if not item.applies(self):
                continue
            if item.section != section:
                section = item.section
                self.echo(f"{section}:")
            if not self.meet(item) and item.required:
                unmet.append(item.name)
        if unmet:
            self.echo(
                f"Host check: {len(unmet)} required item(s) unmet: "
                f"{', '.join(unmet)}. Run `mael self check-host` again "
                "after you fix them."
            )
        else:
            self.echo("Host check: every required item is met.")
        return unmet

    def meet(self, item: HostCheckItem) -> bool:
        """Check one item, and offer its remedy when it is unmet. True when met."""
        finding = item.check(self)
        if finding.met:
            self.echo(f"  ✓ {finding.text}")
            return True
        self.echo(f"  ✗ {finding.text}")
        remedy = item.remedy(self)
        if remedy is None:
            return False
        self.echo(f"    {remedy.hint}")
        if remedy.fix is None:
            return False
        if remedy.question is not None and not (
            self.interactive and self.confirm(remedy.question)
        ):
            return False
        remedy.fix()
        after = item.check(self)
        self.echo(f"  {'✓' if after.met else '✗'} {after.text}")
        return after.met

    @property
    def sudo(self) -> list[str]:
        return ["sudo"] if self.platform == "linux" and not self.root else []

    def lacks_brew(self, command: str) -> bool:
        """Whether ``command`` is a ``brew`` one, on a host with no Homebrew."""
        return command.startswith("brew ") and not self.which("brew")

    def tailscale_running(self) -> bool:
        result = self.run(["tailscale", "status", "--json"])
        try:
            return json.loads(result.stdout).get("BackendState") == "Running"
        except (ValueError, AttributeError):
            return False

    def label(self, path: Path) -> str:
        return f"~/{path.relative_to(self.prod.home)}"
