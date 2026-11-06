"""``HostCheck`` against a fake host: tools, sign-ins, Tailscale and permissions.

Every outside effect is a fake, so nothing installs and nothing signs in. Each
test that differs by platform runs on both.
"""

import json
import os
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from mael_cli.self.host_check import HOST_TOOLS, HostCheck
from mael_cli.self.production import Production
from mael_domain.dev_cert import DevCertError

PLATFORMS = ["darwin", "linux"]
#: A met host: every tool on the default list, and the package manager.
BASE_TOOLS = {
    *("brew", "apt-get", "git", "gh", "uv", "claude", "lsof"),
    *("codex", "tailscale", "tangier"),
}


@dataclass
class FakeHost:
    """A host whose tools, sign-ins and tailnet the test sets."""

    home: Path
    platform: str = "darwin"
    interactive: bool = True
    tools: set[str] = field(default_factory=lambda: set(BASE_TOOLS))
    signed_out: set[str] = field(default_factory=set)
    #: Status commands that exit 0 but report ``loggedIn: false``.
    quietly_signed_out: set[str] = field(default_factory=set)
    tailscale_state: str = "Running"
    operator: str = "sam"
    #: What each command the check runs interactively installs or does.
    installs: dict[str, str] = field(default_factory=dict)
    answer: bool = True
    cert_error: str | None = None
    caller_path: str = ""
    #: Whether minnstack is installed in each harness.
    practices: bool = True
    plugin_commands: list[str] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)

    def which(self, name: str) -> str | None:
        return f"/bin/{name}" if name in self.tools else None

    def run(self, argv) -> subprocess.CompletedProcess:
        argv = list(argv)
        joined = " ".join(argv)
        if argv[1:2] == ["plugin"]:
            self.plugin_commands.append(" ".join([Path(argv[0]).name, *argv[1:]]))
            if argv[2:4] in (
                ["install", "minnstack@maelstrom"],
                ["add", "minnstack@maelstrom"],
            ):
                self.install_practices(Path(argv[0]).name)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["tailscale", "status"]:
            out = json.dumps(
                {
                    "BackendState": self.tailscale_state,
                    "Self": {"DNSName": "box.tail1.ts.net."},
                }
            )
            return subprocess.CompletedProcess(argv, 0, out, "")
        if argv[:3] == ["tailscale", "debug", "prefs"]:
            out = {"OperatorUser": self.operator}
            return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")
        for tool in ("gh", "claude", "codex"):
            if argv[0] == tool:
                code = 1 if tool in self.signed_out else 0
                out = json.dumps(
                    {"loggedIn": code == 0 and tool not in self.quietly_signed_out}
                )
                return subprocess.CompletedProcess(argv, code, out, "")
        raise AssertionError(f"unexpected command: {joined}")

    def run_interactive(self, argv) -> int:
        cmd = argv[-1] if argv[:2] == ["sh", "-c"] else " ".join(argv)
        self.ran.append(cmd)
        if cmd in self.installs:
            self.tools.add(self.installs[cmd])
        return 0

    def confirm(self, text: str) -> bool:
        self.asked.append(text)
        return self.answer

    def issue_cert(self, host: str) -> None:
        if self.cert_error:
            raise DevCertError(self.cert_error)

    def install_practices(self, tool: str) -> None:
        if tool == "claude":
            installed = self.home / ".claude" / "plugins" / "installed_plugins.json"
            installed.parent.mkdir(parents=True, exist_ok=True)
            installed.write_text(json.dumps({"plugins": {"minnstack@maelstrom": []}}))
        else:
            config = self.home / ".codex" / "config.toml"
            config.parent.mkdir(parents=True, exist_ok=True)
            if config.is_file() and "minnstack" in config.read_text():
                return
            with config.open("a") as f:
                f.write('\n[plugins."minnstack@maelstrom"]\nenabled = true\n')

    @property
    def config(self) -> Path:
        return self.home / ".maelstrom" / "config.yaml"

    def check(self, dev: bool = False) -> list[str]:
        if self.practices:
            self.install_practices("claude")
            self.install_practices("codex")
        return HostCheck(
            prod=Production(self.home),
            platform=self.platform,
            interactive=self.interactive,
            which=self.which,
            run=self.run,
            run_interactive=self.run_interactive,
            confirm=self.confirm,
            echo=self.output.append,
            dev=dev,
            caller_path=self.caller_path or str(self.home / ".local" / "bin"),
            user="sam",
            root=False,
            issue_cert=self.issue_cert,
            codex_home=None,
        )()

    @property
    def text(self) -> str:
        return "\n".join(self.output)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".maelstrom").mkdir(mode=0o700)
    (tmp_path / ".maelstrom" / "config.yaml").write_text(
        "dev_host: box.tail1.ts.net\ndev_https: true\n"
    )
    os.chmod(tmp_path / ".maelstrom" / "config.yaml", 0o600)
    return tmp_path


GH_COMMAND = {
    "darwin": "brew install gh",
    "linux": "sudo apt-get install -y gh",
}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_a_missing_tool_installs_after_a_yes(home, platform):
    host = FakeHost(home, platform=platform)
    host.tools.discard("gh")
    host.installs = {cmd: "gh" for cmd in host_commands(platform, "gh")}

    unmet = host.check()

    assert GH_COMMAND[platform] in host.text
    assert len(host.ran) == 1 and GH_COMMAND[platform] in host.ran[0]
    assert unmet == []


def host_commands(platform: str, tool: str) -> list[str]:
    return [c for t in HOST_TOOLS if t.name == tool and (c := t.command(platform))]


def test_a_no_runs_nothing(home):
    host = FakeHost(home, answer=False)
    host.tools.discard("gh")

    unmet = host.check()

    assert host.ran == []
    assert GH_COMMAND["darwin"] in host.text
    assert unmet == ["gh"]


def test_without_a_tty_it_asks_and_runs_nothing(home):
    host = FakeHost(home, platform="linux", interactive=False)
    host.tools -= {"gh", "codex"}
    host.signed_out = {"claude"}
    host.tailscale_state = "Stopped"

    unmet = host.check()

    assert host.asked == [] and host.ran == []
    assert unmet == ["gh", "claude sign-in"]


def test_an_optional_tool_is_not_unmet(home):
    host = FakeHost(home, answer=False)
    host.tools.discard("tangier")

    assert host.check() == []
    assert any("tangier" in q for q in host.asked)


def test_codex_without_node_offers_node_first(home):
    host = FakeHost(home, platform="linux")
    host.tools.discard("codex")
    node = host_commands("linux", "node")[0]
    codex = host_commands("linux", "codex")[0]
    host.installs = {node: "node", codex: "codex"}

    host.check()

    assert host.asked == ["Install node, then codex?"]
    assert host.ran == [node, codex]


def test_codex_waits_when_node_does_not_install(home):
    host = FakeHost(home, platform="linux")
    host.tools.discard("codex")
    node = host_commands("linux", "node")[0]

    host.check()

    assert host.ran == [node]


def test_codex_without_node_or_homebrew_asks_nothing(home):
    host = FakeHost(home, platform="darwin")
    host.tools -= {"brew", "codex"}
    host.answer = False

    host.check()

    assert not any("node" in q for q in host.asked)


def test_dev_adds_node_and_pnpm(home):
    host = FakeHost(home, interactive=False)

    host.check()
    assert "pnpm (optional) is missing" not in host.text
    assert "node (optional) is missing" not in host.text
    host.output.clear()
    host.check(dev=True)

    assert "pnpm (optional) is missing" in host.text
    assert "node (optional) is missing" in host.text


def test_a_brew_command_without_brew_names_homebrew(home):
    host = FakeHost(home, platform="darwin")
    host.tools -= {"brew", "gh"}

    unmet = host.check()

    assert "brew install gh" not in host.ran
    assert "https://brew.sh" in host.text
    assert "gh" in unmet


def test_a_signed_out_gh_signs_in_after_a_yes(home):
    host = FakeHost(home)
    host.signed_out = {"gh"}

    host.check()

    assert "gh auth login" in host.ran


def test_a_status_that_exits_0_but_says_signed_out_is_unmet(home):
    host = FakeHost(home, interactive=False)
    host.quietly_signed_out = {"claude"}

    assert host.check() == ["claude sign-in"]


def test_tailscale_connects_on_linux_and_sets_the_operator(home):
    host = FakeHost(home, platform="linux", tailscale_state="Stopped", operator="")

    host.check()

    assert "sudo tailscale up" in host.ran
    assert "sudo tailscale set --operator=sam" in host.ran


def test_tailscale_connects_on_macos_with_no_operator(home):
    host = FakeHost(home, platform="darwin", tailscale_state="Stopped", operator="")

    host.check()

    assert "tailscale up" in host.ran
    assert not any("--operator" in r for r in host.ran)


@pytest.mark.parametrize("issued", [True, False])
def test_dev_https_is_written_only_when_the_cert_issues(home, issued):
    host = FakeHost(home, cert_error=None if issued else "no HTTPS on this tailnet")
    host.config.write_text("")

    host.check()

    text = host.config.read_text()
    assert "dev_host: box.tail1.ts.net" in text
    assert ("dev_https: true" in text) is issued
    if not issued:
        assert "no HTTPS on this tailnet" in host.text


@pytest.mark.parametrize("platform", PLATFORMS)
def test_a_met_host_asks_nothing(home, platform):
    host = FakeHost(home, platform=platform)

    assert host.check() == []
    assert host.asked == [] and host.ran == []


def test_it_tightens_the_secret_files(home):
    mael = home / ".maelstrom"
    config = mael / "config.yaml"
    os.chmod(mael, 0o755)
    os.chmod(config, 0o644)
    record = mael / "state" / "daemon" / "agents" / "a1.json"
    record.parent.mkdir(parents=True)
    record.write_text("{}")
    os.chmod(record, 0o644)
    host = FakeHost(home, interactive=False)

    host.check()

    for path, mode in ((mael, 0o700), (config, 0o600), (record, 0o600)):
        assert stat.S_IMODE(path.stat().st_mode) == mode
    assert "agents/a1.json" in host.text


def test_it_warns_when_the_shim_is_not_on_path(home):
    host = FakeHost(home, caller_path="/usr/bin")

    host.check()

    assert 'export PATH="$HOME/.local/bin:$PATH"' in host.text


# -- the practices plugin --------------------------------------------------


def test_installed_practices_are_met(home):
    host = FakeHost(home)
    host.check()
    assert "✓ minnstack is installed" in host.text
    assert host.plugin_commands == []


def test_missing_practices_install_after_a_yes(home):
    host = FakeHost(home, practices=False)
    host.check()
    assert "Install minnstack for Claude Code and Codex?" in host.asked
    # The check runs before setup, so it registers the marketplace first.
    link = str(home / ".maelstrom" / "plugins")
    assert host.plugin_commands == [
        f"claude plugin marketplace add {link}",
        "claude plugin install minnstack@maelstrom --scope user",
        "codex plugin add minnstack@maelstrom",
    ]
    assert "✓ minnstack is installed" in host.text


def test_declined_practices_stay_unmet_and_do_not_fail_the_check(home):
    host = FakeHost(home, practices=False, answer=False)
    unmet = host.check()
    assert "✗ minnstack (optional) is not installed" in host.text
    assert host.plugin_commands == []
    assert "minnstack plugin" not in unmet


def test_practices_are_not_checked_without_claude(home):
    host = FakeHost(home, practices=False, tools=set(BASE_TOOLS) - {"claude"})
    host.check()
    assert "minnstack" not in host.text
