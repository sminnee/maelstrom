"""``bin/install-main.sh`` lays out ``_main``, and both installers find the projects directory.

The bootstrap clones a local repo whose ``bin/install.sh`` is a stub that
records its arguments, so no test builds or installs a release. ``install.sh``
runs against a file that is not a bundle, so it stops after the directory step.
A stand-in ``uv`` records its arguments in the same log.

Each script runs in a new session, which has no controlling terminal, so
``/dev/tty`` does not open and no question is asked.

These tests run real ``git`` and ``sh``, but take seconds and touch nothing
outside ``tmp_path``, so they stay in the fast suite.
"""

import os
import subprocess
from pathlib import Path

import pytest
from git_helpers import create_commit, is_detached, run_git, setup_git_repo

from mael_cli.project_check import CheckStatus, run_project_check
from mael_domain.context import read_config_key

REPO = Path(__file__).resolve().parents[1]
INSTALL_MAIN = REPO / "bin" / "install-main.sh"
INSTALL = REPO / "bin" / "install.sh"

STUB = """#!/bin/sh
printf '%s\\n' "$@" >>"$STUB_LOG"
"""


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A ``$HOME`` of the test's own: git and the checks read it."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in (
        *("MAEL_NOTEBOOK_ROOT", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL"),
        # Where uv puts a tool: ensure_tangier looks there first.
        *("UV_TOOL_BIN_DIR", "XDG_BIN_HOME"),
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "uv").write_text('#!/bin/sh\necho "uv $*" >>"$STUB_LOG"\n')
    (tools / "uv").chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    return home


def _make_origin(path: Path, branch: str = "main") -> Path:
    (path / "bin").mkdir(parents=True)
    setup_git_repo(path)
    run_git(path, "checkout", "-q", "-b", branch)
    create_commit(path, "bin/install.sh", STUB, "init")
    return path


@pytest.fixture
def origin(home) -> Path:
    """A repo on ``main``, beside the test's ``$HOME``."""
    return _make_origin(home.parent / "origin")


def _run(script: Path, *args: str, origin: Path | None = None, tmp: Path):
    env = dict(os.environ, STUB_LOG=str(tmp / "stub.log"))
    if origin is not None:
        env["MAEL_REPO_URL"] = origin.as_uri()
    return subprocess.run(
        ["sh", str(script), *args],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        start_new_session=True,
    )


def _config(home: Path) -> str:
    return (home / ".maelstrom" / "config.yaml").read_text()


def _stub_calls(tmp: Path) -> list[str]:
    log = tmp / "stub.log"
    return log.read_text().splitlines() if log.exists() else []


def test_lays_out_main_and_hands_over(tmp_path, home, origin):
    projects = tmp_path / "Projects"
    result = _run(
        INSTALL_MAIN, "--projects-dir", str(projects), origin=origin, tmp=tmp_path
    )
    assert result.returncode == 0, result.stderr

    project = projects / "maelstrom"
    main = project / "_main"
    # The shell layout obeys the same rules as `mael project add`: the bare
    # clone, the refspec, `.mael`, main in _main and its upstream. A check
    # that has to fix something reports FIXED, not OK.
    check = run_project_check(project)
    bad = [c for c in check.checks if c.status is not CheckStatus.OK]
    assert bad == []
    # No check covers the project's HEAD: detached, so main is free for _main.
    assert is_detached(project)

    assert "update_channel: main\n" in _config(home)
    assert f"projects_dir: {projects}\n" in _config(home)
    assert _stub_calls(tmp_path) == [
        "--source",
        str(main),
        "--projects-dir",
        str(projects),
    ]


def test_a_second_run_does_not_clone_again(tmp_path, home, origin):
    projects = tmp_path / "Projects"
    first = _run(
        INSTALL_MAIN, "--projects-dir", str(projects), origin=origin, tmp=tmp_path
    )
    assert first.returncode == 0, first.stderr
    marker = projects / "maelstrom" / "_main" / "local-change"
    marker.write_text("kept")

    # The origin is gone: a second clone would fail. With no flag, the stored
    # key is passed on.
    second = _run(INSTALL_MAIN, "--no-setup", origin=tmp_path / "gone", tmp=tmp_path)

    assert second.returncode == 0, second.stderr
    assert marker.read_text() == "kept"
    hand_over = ["--source", str(projects / "maelstrom" / "_main")]
    hand_over += ["--projects-dir", str(projects)]
    assert _stub_calls(tmp_path) == hand_over + hand_over + ["--no-setup"]
    assert _config(home).count("update_channel:") == 1
    assert _config(home).count("projects_dir:") == 1


def test_a_folder_that_is_not_a_checkout_is_refused(tmp_path, home, origin):
    projects = tmp_path / "Projects"
    other = projects / "maelstrom"
    other.mkdir(parents=True)
    (other / "notes.txt").write_text("mine")

    result = _run(
        INSTALL_MAIN, "--projects-dir", str(projects), origin=origin, tmp=tmp_path
    )

    assert result.returncode != 0
    assert "is not a maelstrom checkout" in result.stderr
    assert sorted(p.name for p in other.iterdir()) == ["notes.txt"]
    assert _stub_calls(tmp_path) == []
    assert "update_channel" not in _config(home)


def test_a_failed_layout_leaves_no_folder_and_no_channel(tmp_path, home):
    # No main branch, so the clone succeeds and a later step fails.
    origin = _make_origin(tmp_path / "origin", branch="trunk")
    projects = tmp_path / "Projects"

    result = _run(
        INSTALL_MAIN, "--projects-dir", str(projects), origin=origin, tmp=tmp_path
    )

    assert result.returncode != 0
    assert not (projects / "maelstrom").exists()
    assert _stub_calls(tmp_path) == []
    assert (
        read_config_key(home / ".maelstrom" / "config.yaml", "update_channel") is None
    )


def test_with_no_flag_and_no_tty_the_stored_key_is_used(tmp_path, home, origin):
    stored = tmp_path / "Stored"
    (home / ".maelstrom").mkdir()
    (home / ".maelstrom" / "config.yaml").write_text(f"projects_dir: {stored}\n")

    result = _run(INSTALL_MAIN, origin=origin, tmp=tmp_path)

    assert result.returncode == 0, result.stderr
    assert (stored / "maelstrom" / "_main").is_dir()
    assert _config(home).count("projects_dir:") == 1


def test_with_no_flag_no_key_and_no_tty_the_default_is_used(tmp_path, home, origin):
    result = _run(INSTALL_MAIN, origin=origin, tmp=tmp_path)

    assert result.returncode == 0, result.stderr
    assert (home / "Projects" / "maelstrom" / "_main").is_dir()
    assert "projects_dir: ~/Projects\n" in _config(home)
    assert _stub_calls(tmp_path)[3] == "~/Projects"


def test_the_two_copies_of_the_directory_functions_are_the_same():
    def block(script: Path) -> list[str]:
        lines = script.read_text().splitlines()
        start = next(
            i for i, x in enumerate(lines) if x.startswith("# --- The projects")
        )
        end = lines.index("# --- End of the copy. ---")
        # The first line names the other script.
        return lines[start + 1 : end]

    assert block(INSTALL) == block(INSTALL_MAIN)


# --- install.sh: the directory step. ---


def _install(tmp: Path, *args: str):
    not_a_bundle = tmp / "not-a-bundle.tar.gz"
    not_a_bundle.write_text("no")
    return _run(
        INSTALL,
        "--bundle",
        str(not_a_bundle),
        "--root",
        str(tmp / "root"),
        *args,
        tmp=tmp,
    )


def test_install_writes_the_flag(tmp_path, home):
    projects = tmp_path / "Projects"
    result = _install(tmp_path, "--projects-dir", str(projects))

    assert "is not a bundle" in result.stderr
    assert _config(home) == f"projects_dir: {projects}\n"
    assert projects.is_dir()


def test_install_keeps_a_stored_key(tmp_path, home):
    (home / ".maelstrom").mkdir()
    config = home / ".maelstrom" / "config.yaml"
    config.write_text("orchestrator_url: http://x\nprojects_dir: ~/Work\n")

    result = _install(tmp_path)

    assert "is not a bundle" in result.stderr
    assert config.read_text() == "orchestrator_url: http://x\nprojects_dir: ~/Work\n"
    assert (home / "Work").is_dir()


@pytest.mark.parametrize("name", ['My "Projects"', "back\\slash", "plain"])
def test_install_replaces_the_key_as_yaml_reads_it(tmp_path, home, name):
    (home / ".maelstrom").mkdir()
    config = home / ".maelstrom" / "config.yaml"
    config.write_text("projects_dir: ~/Work\norchestrator_url: http://x")
    projects = tmp_path / name

    _install(tmp_path, "--projects-dir", str(projects))

    assert read_config_key(config, "projects_dir") == str(projects)
    assert read_config_key(config, "orchestrator_url") == "http://x"
    # The shell reads its own value back: it makes the same directory, and
    # writes nothing.
    before = config.read_text()
    projects.rmdir()
    again = _install(tmp_path)
    assert "is not a bundle" in again.stderr
    assert projects.is_dir()
    assert config.read_text() == before


def test_install_quotes_a_bare_tilde(tmp_path, home):
    """Plain ``~`` is YAML null, which reads as unset."""
    _install(tmp_path, "--projects-dir", "~")

    assert read_config_key(home / ".maelstrom" / "config.yaml", "projects_dir") == "~"


def _source(tmp: Path) -> Path:
    """A checkout whose ``bin/install`` records its arguments, then stops the run."""
    source = tmp / "source"
    (source / "bin").mkdir(parents=True)
    (source / "bin" / "install").write_text(
        '#!/bin/sh\necho "bin/install $*" >>"$STUB_LOG"\nexit 1\n'
    )
    (source / "bin" / "install").chmod(0o755)
    return source


def _stub_tool(tmp: Path, name: str) -> None:
    (tmp / "tools" / name).write_text("#!/bin/sh\n")
    (tmp / "tools" / name).chmod(0o755)


#: The tools a test stubs or leaves out. A host can have each in /usr/bin.
BUILD_TOOLS = {"node", "pnpm", "tangier"}


def _hide_build_tools(tmp: Path, monkeypatch) -> None:
    """PATH: the stub tools, then the system's commands without the build tools."""
    system = tmp / "system"
    system.mkdir()
    for folder in (Path("/bin"), Path("/usr/bin")):
        for command in folder.iterdir():
            target = system / command.name
            if command.name not in BUILD_TOOLS and not target.exists():
                target.symlink_to(command)
    monkeypatch.setenv("PATH", f"{tmp / 'tools'}{os.pathsep}{system}")


def _source_args(tmp: Path) -> list[str]:
    source = _source(tmp)
    return [
        *("--source", str(source), "--root", str(tmp / "root"), "--no-pull"),
        *("--projects-dir", str(tmp / "P")),
    ]


@pytest.mark.parametrize("installed", [False, True], ids=["first", "rerun"])
def test_install_source_offers_the_host_tools_before_the_build(
    tmp_path, home, installed
):
    """A rerun after a failed build checks the host too: it once looked like an update."""
    for tool in BUILD_TOOLS:
        _stub_tool(tmp_path, tool)
    args = _source_args(tmp_path)
    if installed:
        (tmp_path / "root" / "current").mkdir(parents=True)
        (tmp_path / "root" / "current" / "manifest.toml").write_text("")

    _run(INSTALL, *args, tmp=tmp_path)
    # Only setup migrates the production state database.
    assert _stub_calls(tmp_path) == [
        f"uv run --project {tmp_path / 'source'} mael self check-host --dev --quiet",
        "bin/install --no-migrate",
    ]

    (tmp_path / "stub.log").unlink()
    _run(INSTALL, *args, "--no-setup", tmp=tmp_path)
    assert _stub_calls(tmp_path) == ["bin/install --no-migrate"]


@pytest.mark.parametrize("missing", ["node", "pnpm"])
def test_install_source_stops_before_the_build_without_node_or_pnpm(
    tmp_path, home, monkeypatch, missing
):
    for tool in BUILD_TOOLS - {missing}:
        _stub_tool(tmp_path, tool)
    _hide_build_tools(tmp_path, monkeypatch)

    result = _run(INSTALL, *_source_args(tmp_path), tmp=tmp_path)

    assert result.returncode != 0
    assert f"{missing} is not on PATH" in result.stderr
    assert not any(c.startswith("bin/install") for c in _stub_calls(tmp_path))


def _uv_that_installs_tangier(tmp: Path, then: str) -> None:
    (tmp / "tools" / "uv").write_text(
        f'#!/bin/sh\necho "uv $*" >>"$STUB_LOG"\n{then}\n'
    )


def test_install_source_installs_a_missing_tangier_with_uv(tmp_path, home, monkeypatch):
    for tool in ("node", "pnpm"):
        _stub_tool(tmp_path, tool)
    # The stand-in uv puts tangier where uv does: ~/.local/bin, not on PATH.
    _uv_that_installs_tangier(
        tmp_path,
        f'mkdir -p "$HOME/.local/bin" && cp {tmp_path / "tools" / "node"} '
        '"$HOME/.local/bin/tangier"',
    )
    _hide_build_tools(tmp_path, monkeypatch)

    _run(INSTALL, *_source_args(tmp_path), "--no-setup", tmp=tmp_path)

    assert _stub_calls(tmp_path) == [
        "uv tool install tangier",
        "bin/install --no-migrate",
    ]


def test_install_source_stops_when_uv_cannot_install_tangier(
    tmp_path, home, monkeypatch
):
    for tool in ("node", "pnpm"):
        _stub_tool(tmp_path, tool)
    _uv_that_installs_tangier(tmp_path, "exit 1")
    _hide_build_tools(tmp_path, monkeypatch)

    result = _run(INSTALL, *_source_args(tmp_path), "--no-setup", tmp=tmp_path)

    assert result.returncode != 0
    assert "Install tangier with `uv tool install tangier`" in result.stderr
    assert _stub_calls(tmp_path) == ["uv tool install tangier"]


MIGRATE = "uv run python -m mael_domain.state_db.migrate"


@pytest.mark.parametrize(("args", "migrates"), [((), True), (("--no-migrate",), False)])
def test_bin_install_migrates_unless_told_not_to(tmp_path, home, args, migrates):
    """A source install must not migrate the production state database."""
    _stub_tool(tmp_path, "pnpm")

    result = _run(REPO / "bin" / "install", *args, tmp=tmp_path)

    assert result.returncode == 0, result.stderr
    assert (MIGRATE in _stub_calls(tmp_path)) is migrates


def test_bin_install_refuses_an_unknown_argument(tmp_path, home):
    result = _run(REPO / "bin" / "install", "--bogus", tmp=tmp_path)

    assert result.returncode == 1
    assert "Usage: bin/install [--no-migrate]" in result.stderr
    assert _stub_calls(tmp_path) == []


def test_install_refuses_a_relative_folder(tmp_path, home):
    result = _install(tmp_path, "--projects-dir", "Projects")

    assert result.returncode != 0
    assert "is relative" in result.stderr
    assert not (home / ".maelstrom" / "config.yaml").exists()
