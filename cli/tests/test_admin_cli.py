"""Tests for self-management CLI commands."""

import asyncio
import os
import subprocess
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import click
import pytest
from click.testing import CliRunner, Result

from mael_cli.admin_cli import cmd_export_queue, cmd_self_update
from mael_domain import task as task_model
from mael_domain.env import EnvRefresh, EnvState, VersionChange
from mael_domain.release_bundle.install import InstallReport, Manifest
from mael_domain.state_db.migrate import open_state_db
from mael_domain.task_export import SqliteExportQueue
from mael_domain.task_table import SqliteTaskTable
from mael_domain.worktree_model import CopyBackResult


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=0, stdout=stdout, stderr=stderr
    )


def _fail(stderr: str = "boom") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


COMMIT = "abcdef1234567890"


@dataclass
class Runner:
    """A fake ``subprocess.run``: a reply per command prefix, else success."""

    replies: dict[str, subprocess.CompletedProcess] = field(default_factory=dict)
    calls: list[tuple[list[str], dict]] = field(default_factory=list)

    def __call__(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        self.calls.append((cmd, kwargs))
        line = " ".join(cmd)
        reply = next(
            (r for prefix, r in self.replies.items() if line.startswith(prefix)),
            _ok(),
        )
        if kwargs.get("check") and reply.returncode != 0:
            raise subprocess.CalledProcessError(
                reply.returncode, cmd, reply.stdout, reply.stderr
            )
        return reply

    def commands(self) -> list[str]:
        return [" ".join(cmd) for cmd, _ in self.calls]


class Host:
    """A home directory with a config, a `_main` checkout and an install root.

    Every outside effect is a fake: the commands, the installer, the Claude
    integration, the restart and tangier.
    """

    def __init__(self, home: Path) -> None:
        self.home = home
        self.maelstrom = home / ".maelstrom"
        self.maelstrom.mkdir()
        self.config = self.maelstrom / "config.yaml"
        self.config.write_text(f"# mine\nprojects_dir: {home / 'Projects'}\n")
        self.main = home / "Projects" / "maelstrom" / "_main"
        (self.main / ".git").mkdir(parents=True)
        self.root = self.maelstrom / "install"
        self.shim = home / ".local" / "bin" / "mael"
        self.runner = Runner(
            {
                "gh release view": _ok("v0.3.0\n"),
                "git rev-parse HEAD": _ok(COMMIT + "\n"),
            }
        )
        self.report = InstallReport("v0.3.0", changed=["orchestrator"], unchanged=[])
        self.install = MagicMock()
        self.run_install = MagicMock()
        self.restart = MagicMock(return_value=[])
        self.jig = [None, None]
        self.pnpm: str | None = "/usr/bin/pnpm"

    def running(self, tag: str) -> None:
        """Make ``tag`` the release that runs now."""
        release = self.root / "releases" / tag
        release.mkdir(parents=True)
        Manifest(tag=tag, commit="c", dirty=False).write(release / "manifest.toml")
        (self.root / "current").symlink_to(release)

    def update(self, *args: str) -> Result:
        self.install.return_value = self.report
        jig = iter(self.jig)
        with (
            patch("mael_cli.admin_cli.subprocess.run", side_effect=self.runner),
            patch("mael_cli.admin_cli.install_bundle", self.install),
            patch("mael_cli.admin_cli.install_claude_integration", return_value=[]),
            patch("mael_cli.admin_cli.harden_global_config", return_value=[]),
            patch("mael_cli.admin_cli.run_install_cmd", self.run_install),
            patch("mael_cli.admin_cli.restart_changed", self.restart),
            patch(
                "mael_cli.admin_cli.service_versions",
                side_effect=lambda *_: {"jig": v} if (v := next(jig)) else {},
            ),
            patch("mael_cli.admin_cli.shutil.which", return_value=self.pnpm),
        ):
            return CliRunner().invoke(cmd_self_update, list(args))


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return Host(tmp_path)


class TestSelfUpdateStable:
    """The stable channel installs the latest GitHub Release."""

    def test_it_downloads_the_latest_release_and_installs_it(self, host):
        result = host.update()

        assert result.exit_code == 0, result.output
        commands = host.runner.commands()
        assert commands[0].startswith("gh release view --repo sminnee/maelstrom")
        assert commands[1].startswith(
            "gh release download v0.3.0 --repo sminnee/maelstrom "
            "--pattern maelstrom.tar.gz --dir "
        )
        bundle, root = host.install.call_args.args
        assert bundle.name == "maelstrom.tar.gz"
        assert root == host.root
        assert "Installed v0.3.0. Changed: orchestrator." in result.output
        assert not [c for c in commands if c.startswith("git ")]
        host.restart.assert_not_called()
        assert "Update complete." in result.output

    def test_the_release_that_runs_already_installs_nothing(self, host):
        host.running("v0.3.0")

        result = host.update()

        assert result.exit_code == 0, result.output
        assert "v0.3.0 is installed already." in result.output
        assert not [c for c in host.runner.commands() if "download" in c]
        host.install.assert_not_called()
        assert "Update complete." in result.output

    def test_a_tag_installs_that_release(self, host):
        host.running("v0.3.0")

        result = host.update("--tag", "v0.2.0rc1")

        assert result.exit_code == 0, result.output
        [download] = host.runner.commands()
        assert download.startswith("gh release download v0.2.0rc1 ")
        host.install.assert_called_once()

    def test_a_tag_needs_the_stable_channel(self, host):
        result = host.update("--channel", "main", "--tag", "v0.2.0")

        assert result.exit_code != 0
        assert "--channel stable" in result.output
        host.install.assert_not_called()

    def test_a_failed_download_names_gh_s_reason(self, host):
        host.runner.replies["gh release download"] = _fail("HTTP 404: Not Found")

        result = host.update()

        assert result.exit_code != 0
        assert "HTTP 404: Not Found" in result.output
        host.install.assert_not_called()


class TestSelfUpdateMain:
    """The main channel builds a bundle from `_main` with `_main`'s own script."""

    def test_it_pulls_builds_installs_and_restarts_main(self, host):
        host.restart.return_value = [VersionChange("agent-daemon", "a1", "a2")]

        result = host.update("--channel", "main")

        assert result.exit_code == 0, result.output
        commands = host.runner.commands()
        assert "git pull" in commands
        pull = host.runner.calls[commands.index("git pull")]
        assert pull[1]["cwd"] == host.main
        host.run_install.assert_called_once_with(host.main)
        build = next(c for c in commands if "build-bundle" in c)
        assert build.startswith(
            f"{host.main / 'bin' / 'build-bundle'} --tag main-abcdef123456 --out "
        )
        host.install.assert_called_once()
        host.restart.assert_called_once_with(ANY, "maelstrom", "_main", host.main)
        assert "agent-daemon: a1 → a2" in result.output
        assert "The agent daemon restarted. Mid-turn agents resume." in result.output

    def test_the_channel_is_stored(self, host):
        result = host.update("--channel", "main")

        assert result.exit_code == 0, result.output
        assert "update_channel: main\n" in host.config.read_text()

    def test_a_refused_tag_stores_no_channel(self, host):
        result = host.update("--channel", "main", "--tag", "v0.3.0")

        assert result.exit_code != 0
        assert "update_channel" not in host.config.read_text()

    def test_the_stored_channel_is_used(self, host):
        host.config.write_text(host.config.read_text() + "update_channel: main\n")

        result = host.update()

        assert result.exit_code == 0, result.output
        assert "git pull" in host.runner.commands()
        assert not [c for c in host.runner.commands() if c.startswith("gh ")]

    def test_the_commit_that_runs_already_is_not_built(self, host):
        host.running("main-abcdef123456")

        result = host.update("--channel", "main")

        assert result.exit_code == 0, result.output
        assert not [c for c in host.runner.commands() if "build-bundle" in c]
        host.install.assert_not_called()

    def test_a_failed_install_command_builds_nothing(self, host):
        """The install migrates the state database, which new code refuses unmigrated."""
        host.run_install.side_effect = subprocess.CalledProcessError(1, "sh")

        result = host.update("--channel", "main")

        assert result.exit_code != 0
        assert "install_cmd failed" in result.output
        assert not [c for c in host.runner.commands() if "build-bundle" in c]
        host.install.assert_not_called()
        host.restart.assert_not_called()

    def test_a_failed_restart_names_what_may_be_down(self, host):
        host.restart.side_effect = TimeoutError("no address")

        result = host.update("--channel", "main")

        assert result.exit_code == 0, result.output
        assert "mael self-env start" in result.output
        assert "Update complete." in result.output

    def test_without_a_main_checkout_it_refuses(self, host):
        (host.main / ".git").rmdir()

        result = host.update("--channel", "main")

        assert result.exit_code != 0
        assert "not a checkout" in result.output


class TestSelfUpdateLinksTheJig:
    """A pull that moves the jig plugin builds `_main`'s copy and links it.

    Then `pnpm link --global vite-plugin-mael-jig` in a project uses it in
    place of the npm release.
    """

    def _pnpm(self, host) -> list[tuple[list[str], dict]]:
        return [c for c in host.runner.calls if c[0][0] == "/usr/bin/pnpm"]

    def test_a_moved_jig_is_built_and_linked(self, host):
        host.jig = ["j1", "j2"]

        result = host.update("--channel", "main")

        assert result.exit_code == 0, result.output
        steps = self._pnpm(host)
        assert [cmd for cmd, _ in steps] == [
            ["/usr/bin/pnpm", "install", "--frozen-lockfile"],
            ["/usr/bin/pnpm", "run", "build"],
            ["/usr/bin/pnpm", "link", "--global"],
        ]
        assert {kw["cwd"] for _, kw in steps} == {host.main / "vite-plugin-mael-jig"}

    @pytest.mark.parametrize("jig", [["j1", "j1"], ["j1", None]])
    def test_an_unmoved_or_unhashed_jig_is_not_linked(self, host, jig):
        host.jig = jig

        host.update("--channel", "main")

        assert self._pnpm(host) == []

    def test_a_failed_build_warns_and_does_not_link(self, host):
        host.jig = ["j1", "j2"]
        host.runner.replies["/usr/bin/pnpm run build"] = _fail("tsc error")

        result = host.update("--channel", "main")

        assert result.exit_code == 0, result.output
        assert len(self._pnpm(host)) == 2
        assert "tsc error" in result.output
        assert "the jig plugin is not linked" in result.output

    def test_warns_and_skips_the_link_when_pnpm_is_missing(self, host):
        host.jig = ["j1", "j2"]
        host.pnpm = None

        result = host.update("--channel", "main")

        assert result.exit_code == 0, result.output
        assert "'pnpm' not found" in result.output


class TestSelfUpdateWritesTheShim:
    """The `mael` on PATH runs the `current` release, with the everyday roots.

    A worktree's `.env` and the root a daemon gives its children both still
    win: each arrives with a root already set.
    """

    @staticmethod
    def _run_shim(host: Host, **env: str) -> list[str]:
        """Run the shim against a `mael` that prints its roots and arguments."""
        mael = host.root / "current" / "venv" / "bin" / "mael"
        mael.parent.mkdir(parents=True, exist_ok=True)
        mael.write_text(
            '#!/bin/sh\necho "$MAEL_AGENT_ROOT|$MAEL_NOTEBOOK_ROOT|'
            '$MAEL_PRODUCTION|$*"\n'
        )
        mael.chmod(0o755)
        printed = subprocess.run(
            [str(host.shim), "task", "list"],
            env={"PATH": os.environ["PATH"], **env},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return printed.strip().split("|")

    def test_the_shim_runs_the_current_release_with_the_everyday_roots(self, host):
        result = host.update()

        assert result.exit_code == 0, result.output
        assert self._run_shim(host) == [
            str(host.maelstrom / "daemons" / "_main"),
            str(host.maelstrom),
            "1",
            "task list",
        ]

    def test_a_root_already_set_wins(self, host):
        host.update()

        roots = self._run_shim(host, MAEL_AGENT_ROOT="/w", MAEL_NOTEBOOK_ROOT="/n")

        assert roots[:2] == ["/w", "/n"]

    def test_a_symlinked_mael_is_replaced_not_written_through(self, host):
        """uv's tool entry point can be a symlink into the tool's venv."""
        target = host.home / "uv-tool-mael"
        target.write_text("#!/bin/sh\nexec real\n")
        host.shim.parent.mkdir(parents=True)
        host.shim.symlink_to(target)

        host.update()

        assert not host.shim.is_symlink()
        assert target.read_text() == "#!/bin/sh\nexec real\n"

    def test_an_unwritable_bin_warns_rather_than_aborting(self, host):
        """The release is installed by this point."""
        host.shim.parent.mkdir(parents=True)
        host.shim.parent.chmod(0o500)
        try:
            result = host.update()
        finally:
            host.shim.parent.chmod(0o700)

        assert result.exit_code == 0, result.output
        assert "could not write" in result.output
        assert "Update complete." in result.output
        assert os.listdir(host.shim.parent) == []


class TestSelfEnv:
    """`mael self-env` is `mael env` aimed at the maelstrom project's `_main`."""

    def _invoke(self, args, projects_dir):
        from mael_cli.cli import cli

        with patch("mael_domain.context.load_global_config") as mock_global:
            mock_global.return_value = MagicMock(projects_dir=projects_dir)
            return CliRunner().invoke(cli, args)

    # Each verb, and the model function that must receive ("maelstrom", "_main").
    # `logs` and `open` take the target through a state lookup, not an argument,
    # so they assert on the project/worktree that lookup was given.
    VERBS = [
        ("start", "start_env"),
        ("stop", "stop_env"),
        ("restart", "restart_services"),
        ("status", "get_env_status"),
        ("reset", "refresh_env"),
        ("logs", "get_log_files"),
        ("open", "load_env_state"),
    ]

    @pytest.mark.parametrize("verb,target_fn", VERBS)
    def test_every_verb_targets_maelstrom_main(self, verb, target_fn, tmp_path):
        """Every wrapped verb names ("maelstrom", "_main") to the model."""
        (tmp_path / "maelstrom" / "_main").mkdir(parents=True)
        state = EnvState(
            project="maelstrom",
            worktree="_main",
            worktree_path=str(tmp_path / "maelstrom" / "_main"),
            started_at="2026-01-01T00:00:00+00:00",
            services=[],
        )

        defaults = {
            "start_env": state,
            "stop_env": [],
            "get_env_status": [],
            "load_env_state": state,
            "get_app_url": None,
            "get_log_files": {},
            "read_service_logs": "",
            "refresh_env": EnvRefresh(CopyBackResult(), changed=True),
            "restart_services": ([], state),
            "ensure_cmux_browser": None,
            "update_claude_local_md": None,
        }

        patches = {
            name: patch(f"mael_cli.env_cli.{name}", return_value=value)
            for name, value in defaults.items()
        }
        with ExitStack() as stack:
            mocks = {n: stack.enter_context(p) for n, p in patches.items()}
            result = self._invoke(["self-env", verb], tmp_path)

        assert result.exit_code == 0, result.output
        target = mocks[target_fn]
        assert target.called, f"{verb} never reached {target_fn}"
        args = target.call_args.args
        assert args[1:3] == ("maelstrom", "_main"), f"{verb} passed {args[1:3]}"

    def test_start_passes_the_main_worktree_path(self, tmp_path):
        """The resolved path is the `_main` folder, with no project prefix."""
        main_path = tmp_path / "maelstrom" / "_main"
        main_path.mkdir(parents=True)

        with (
            patch("mael_cli.env_cli.start_env") as start,
            patch("mael_cli.env_cli.get_env_status", return_value=[]),
            patch("mael_cli.env_cli.load_env_state", return_value=None),
            patch("mael_cli.env_cli.get_app_url", return_value=None),
        ):
            start.return_value = MagicMock(services=[])
            result = self._invoke(["self-env", "start"], tmp_path)

        assert result.exit_code == 0, result.output
        assert start.call_args.args[3] == main_path

    def test_it_wraps_exactly_the_verbs_that_take_a_worktree(self):
        """The `env` verbs with -w, and no others."""
        from mael_cli.admin_cli import cmd_self_env

        commands = cmd_self_env.list_commands(click.Context(cmd_self_env))

        assert set(commands) == {verb for verb, _ in self.VERBS}

    @pytest.mark.parametrize("verb", [verb for verb, _ in VERBS])
    def test_help_hides_the_fixed_target(self, verb, tmp_path):
        """Neither -w nor the deprecated TARGET shows in a wrapped verb's help."""
        result = self._invoke(["self-env", verb, "--help"], tmp_path)

        assert result.exit_code == 0, result.output
        assert "-w" not in result.output
        assert "TARGET" not in result.output

    @pytest.mark.parametrize("verb", [verb for verb, _ in VERBS])
    def test_the_fixed_target_cannot_be_redirected(self, verb, tmp_path):
        """A -w of the user's own is refused, not a silent retarget."""
        with patch("mael_cli.env_cli.resolve_context") as mock_ctx:
            result = self._invoke(["self-env", verb, "-w", "other.b"], tmp_path)

        assert result.exit_code != 0
        assert "No such option" in result.output
        assert not mock_ctx.called

    def test_a_stray_argument_names_itself_in_the_error(self, tmp_path):
        """The fixed target must not be blamed for the user's own typo."""
        (tmp_path / "maelstrom" / "_main").mkdir(parents=True)

        result = self._invoke(["self-env", "status", "typo"], tmp_path)

        assert result.exit_code != 0
        assert "typo" in result.output
        assert "maelstrom._main" not in result.output


class TestExportQueue:
    """`mael admin export-queue` reports what the markdown export still owes."""

    @pytest.mark.usefixtures("migrated_notebook")
    def test_a_caught_up_export_says_so(self):
        result = CliRunner().invoke(cmd_export_queue, [])

        assert result.exit_code == 0, result.output
        assert "up to date" in result.output

    def test_it_reports_the_depth_and_the_oldest_entry(self, migrated_notebook):
        """The two numbers that tell a stalled drain from a busy notebook."""

        db = open_state_db(migrated_notebook / "state.db")
        try:
            table = SqliteTaskTable(db)
            asyncio.run(
                task_model.create(
                    table, project="northwind", title="Ship it", id="NORT-7"
                )
            )
        finally:
            db.close()

        result = CliRunner().invoke(cmd_export_queue, [])

        assert result.exit_code == 0, result.output
        assert "owes 1 task(s)" in result.output
        assert "oldest has waited since" in result.output

    def test_rebuild_queues_every_task(self, migrated_notebook):
        """The recovery path: a file that went missing without its row moving.

        A drained queue is the starting state, because that is when a lost
        export file is unrecoverable — no later save re-queues it.
        """

        db = open_state_db(migrated_notebook / "state.db")
        try:
            table = SqliteTaskTable(db)
            for id in ("NORT-7", "NORT-8"):
                asyncio.run(
                    task_model.create(table, project="northwind", title=id, id=id)
                )
            asyncio.run(SqliteExportQueue(db).queue_all([]))
            for id in ("NORT-7", "NORT-8"):
                asyncio.run(SqliteExportQueue(db).clear(f"northwind/{id}"))
            assert asyncio.run(SqliteExportQueue(db).depth()) == 0
        finally:
            db.close()

        result = CliRunner().invoke(cmd_export_queue, ["--rebuild"])

        assert result.exit_code == 0, result.output
        assert "Queued 2 task(s)" in result.output

        db = open_state_db(migrated_notebook / "state.db")
        try:
            queued = asyncio.run(SqliteExportQueue(db).pending())
        finally:
            db.close()
        assert [entry.id for entry in queued] == [
            "northwind/NORT-7",
            "northwind/NORT-8",
        ]
        assert [entry.path for entry in queued] == [
            "northwind/todo/NORT-7.md",
            "northwind/todo/NORT-8.md",
        ]

    @pytest.mark.usefixtures("migrated_notebook")
    def test_rebuild_on_an_empty_notebook_queues_nothing(self):
        result = CliRunner().invoke(cmd_export_queue, ["--rebuild"])

        assert result.exit_code == 0, result.output
        assert "Queued 0 task(s)" in result.output

    def test_a_database_behind_this_build_is_refused(self, tmp_path, monkeypatch):
        """An ordinary open never migrates, so the check names the command."""
        monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path))
        open_state_db(tmp_path / "state.db").close()

        result = CliRunner().invoke(cmd_export_queue, [])

        assert result.exit_code != 0
        assert "bin/install" in result.output
