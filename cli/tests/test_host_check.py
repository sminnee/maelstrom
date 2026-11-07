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

from mael_cli.self.host_check import (
    HOST_TOOLS,
    HostCheck,
    chrome_glob,
    chrome_profile_text,
)
from mael_cli.self.production import Production
from mael_domain.dev_cert import DevCertError

PLATFORMS = ["darwin", "linux"]
#: A met host: every tool on the default list, and the package manager.
BASE_TOOLS = {
    *("brew", "apt-get", "git", "gh", "uv", "claude", "lsof"),
    *("codex", "tailscale", "tangier", "docker", "agent-browser"),
}
SKILL_DIRS = (".claude/skills/agent-browser", ".agents/skills/agent-browser")
SKILL_COMMAND = (
    "npx -y skills add vercel-labs/agent-browser -g -a claude-code -a codex -y"
)


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
    #: Whether the agent-browser skill is installed.
    skill: bool = True
    plugin_commands: list[str] = field(default_factory=list)
    git_config: dict[str, str] = field(
        default_factory=lambda: {"user.name": "Sam", "user.email": "sam@example.com"}
    )
    gh_user: dict = field(default_factory=lambda: {"login": "sam", "id": 7})
    #: The answer to each prompt, by its text. An unknown prompt takes the default.
    answers: dict[str, str] = field(default_factory=dict)
    prompted: list[tuple[str, str | None]] = field(default_factory=list)
    #: ``docker info``: its exit code and stderr.
    docker_info: tuple[int, str] = (0, "")
    #: ``kernel.apparmor_restrict_unprivileged_userns``.
    userns_restricted: str = "0"
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
        if argv[:3] == ["git", "config", "--global"]:
            if len(argv) == 5:
                self.git_config[argv[3]] = argv[4]
                return subprocess.CompletedProcess(argv, 0, "", "")
            value = self.git_config.get(argv[3])
            return subprocess.CompletedProcess(argv, 0 if value else 1, value or "", "")
        if argv == ["gh", "api", "user"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.gh_user), "")
        if argv == ["docker", "info"]:
            code, err = self.docker_info
            return subprocess.CompletedProcess(argv, code, "", err)
        if argv[:2] == ["sysctl", "-n"]:
            return subprocess.CompletedProcess(
                argv, 0, f"{self.userns_restricted}\n", ""
            )
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
        if cmd == SKILL_COMMAND:
            self.install_skill()
        return 0

    def install_skill(self) -> None:
        for d in SKILL_DIRS:
            (self.home / d).mkdir(parents=True, exist_ok=True)
            (self.home / d / "SKILL.md").write_text("---\nname: agent-browser\n---\n")

    def confirm(self, text: str) -> bool:
        self.asked.append(text)
        return self.answer

    def prompt(self, text: str, default: str | None) -> str:
        self.prompted.append((text, default))
        return self.answers.get(text, default or "")

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
        if self.skill:
            self.install_skill()
        return HostCheck(
            prod=Production(self.home),
            platform=self.platform,
            interactive=self.interactive,
            which=self.which,
            run=self.run,
            run_interactive=self.run_interactive,
            confirm=self.confirm,
            echo=self.output.append,
            prompt=self.prompt,
            dev=dev,
            caller_path=self.caller_path or str(self.home / ".local" / "bin"),
            user="sam",
            root=False,
            issue_cert=self.issue_cert,
            codex_home=None,
            etc=self.home / "etc",
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


# -- git identity ----------------------------------------------------------


def test_a_missing_git_identity_is_set_with_defaults_from_gh(home):
    host = FakeHost(home, git_config={})
    host.gh_user = {"login": "sam", "id": 7, "name": "Sam Minnée", "email": None}
    host.answers = {"Git author email": "sam@example.com"}

    assert host.check() == []

    assert host.prompted == [
        ("Git author name", "Sam Minnée"),
        ("Git author email", "7+sam@users.noreply.github.com"),
    ]
    assert host.git_config == {
        "user.name": "Sam Minnée",
        "user.email": "sam@example.com",
    }
    assert "✓ Sam Minnée <sam@example.com>" in host.text


def test_a_set_git_value_is_not_asked_for_again(home):
    host = FakeHost(home, git_config={"user.name": "Sam"})
    host.gh_user = {"login": "sam", "id": 7, "email": "sam@gh.example"}

    host.check()

    assert host.prompted == [("Git author email", "sam@gh.example")]
    assert host.git_config == {"user.name": "Sam", "user.email": "sam@gh.example"}


def test_without_gh_the_git_identity_has_no_defaults(home):
    host = FakeHost(home, git_config={}, answer=True)
    host.tools.discard("gh")
    host.answers = {"Git author name": "Sam", "Git author email": "sam@example.com"}

    host.check()

    assert host.prompted == [("Git author name", None), ("Git author email", None)]
    assert host.git_config == {"user.name": "Sam", "user.email": "sam@example.com"}


def test_without_a_tty_a_missing_git_identity_fails_the_check(home):
    host = FakeHost(home, git_config={}, interactive=False)

    assert host.check() == ["git identity"]
    assert host.prompted == []


# -- docker ----------------------------------------------------------------


def test_docker_not_installed_is_optional(home):
    host = FakeHost(home, answer=False)
    host.tools.discard("docker")

    assert host.check() == []
    assert "docker (optional) is missing" in host.text
    assert "Docker:" not in host.text


def test_a_stopped_docker_on_macos_opens_docker_desktop(home):
    host = FakeHost(home, platform="darwin", docker_info=(1, "Cannot connect"))

    assert host.check() == []
    assert host.ran == ["open -a Docker"]


def test_a_stopped_docker_on_linux_starts_the_service(home):
    host = FakeHost(home, platform="linux", docker_info=(1, "Cannot connect"))

    host.check()

    assert host.ran == ["sudo systemctl enable --now docker"]


def test_a_denied_docker_socket_adds_the_user_to_the_group(home):
    denied = "permission denied while trying to connect to the Docker daemon socket"
    host = FakeHost(home, platform="linux", docker_info=(1, denied))

    unmet = host.check()

    assert host.ran == ["sudo usermod -aG docker sam"]
    assert "log out and in again" in host.text
    assert unmet == []


# -- the agent-browser skill and Chrome's sandbox --------------------------


def test_a_missing_agent_browser_skill_installs_after_a_yes(home):
    host = FakeHost(home, skill=False)

    host.check()

    assert host.ran == [SKILL_COMMAND]
    assert "✓ the agent-browser skill is installed" in host.text


def test_a_skill_for_claude_code_alone_installs_for_codex_too(home):
    host = FakeHost(home, skill=False)
    claude = home / SKILL_DIRS[0]
    claude.mkdir(parents=True)
    (claude / "SKILL.md").write_text("skill")

    host.check()

    assert host.ran == [SKILL_COMMAND]


def test_the_skill_is_not_checked_without_agent_browser(home):
    host = FakeHost(home, skill=False, answer=False)
    host.tools.discard("agent-browser")

    host.check()

    assert "agent-browser skill" not in host.text


def test_a_restricted_userns_adds_the_chrome_apparmor_profile(home):
    host = FakeHost(home, platform="linux", userns_restricted="1")

    host.check()

    [script] = host.ran
    profile = home / "etc" / "apparmor.d" / "agent-browser-chrome"
    assert f"sudo tee {profile}" in script
    assert f"sudo apparmor_parser -r {profile}" in script
    # The test's home is not under /home, so the profile names it alone.
    assert f"{home}/.agent-browser/browsers/chrome-*/chrome flags" in script


def test_an_existing_chrome_profile_is_met(home):
    host = FakeHost(home, platform="linux", userns_restricted="1")
    (home / "etc" / "apparmor.d").mkdir(parents=True)
    (home / "etc" / "apparmor.d" / "agent-browser-chrome").write_text(
        chrome_profile_text(home)
    )

    host.check()

    assert host.ran == []
    assert "✓ AppArmor lets Chrome make its sandbox" in host.text


@pytest.mark.parametrize(("platform", "restricted"), [("linux", "0"), ("darwin", "1")])
def test_the_chrome_profile_applies_only_on_restricted_linux(
    home, platform, restricted
):
    host = FakeHost(home, platform=platform, userns_restricted=restricted)

    host.check()

    assert "AppArmor" not in host.text


def test_the_chrome_profile_does_not_apply_without_agent_browser(home):
    host = FakeHost(home, platform="linux", userns_restricted="1", answer=False)
    host.tools.discard("agent-browser")

    host.check()

    assert "AppArmor" not in host.text


def test_a_profile_for_another_home_is_unmet(home):
    host = FakeHost(home, platform="linux", userns_restricted="1", answer=False)
    (home / "etc" / "apparmor.d").mkdir(parents=True)
    (home / "etc" / "apparmor.d" / "agent-browser-chrome").write_text(
        chrome_profile_text(Path("/srv/users/other"))
    )

    host.check()

    assert "✗ AppArmor stops agent-browser's Chrome" in host.text


@pytest.mark.parametrize(
    ("home_dir", "glob"),
    [
        ("/home/sam", "/home/*/.agent-browser/browsers/chrome-*/chrome"),
        ("/root", "/root/.agent-browser/browsers/chrome-*/chrome"),
    ],
)
def test_chrome_glob_covers_every_home_under_home(home_dir, glob):
    assert chrome_glob(Path(home_dir)) == glob
