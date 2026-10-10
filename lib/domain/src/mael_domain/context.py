"""Context resolution for maelstrom commands.

This module handles resolving project and worktree context from:
- Explicit command-line arguments (project.worktree format)
- Current working directory detection
- Global configuration (~/.maelstrom/config.yaml)
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mael_common.util import get_maelstrom_dir, harden_path

GLOBAL_CONFIG_FILENAME = "config.yaml"
GLOBAL_CONFIG_FILENAME_LEGACY = ".maelstrom.yaml"


#: A bare host name or IPv4 address: no scheme, port, path or whitespace.
_HOST_NAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?")


def _host_name(value: object) -> str | None:
    """``value`` stripped when it is a bare host name, else ``None``."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _HOST_NAME.fullmatch(value) else None


@dataclass
class GlobalConfig:
    """Global maelstrom configuration from ~/.maelstrom/config.yaml."""

    projects_dir: Path
    open_command: str = "code"
    #: The host reported URLs name. ``None`` means ``localhost``.
    dev_host: str | None = None
    #: Serve dev envs over HTTPS with a tailnet certificate. Needs ``dev_host``.
    dev_https: bool = False
    linear_api_key: str | None = None
    sentry_api_key: str | None = None
    uptimerobot_api_key: str | None = None
    openai_api_key: str | None = None
    github_orchestrator_token: str | None = None
    slack_webhooks: dict[str, str] = field(default_factory=dict)

    @property
    def url_host(self) -> str:
        """The host every reported URL names: the dev host, or ``localhost``."""
        return self.dev_host or "localhost"

    @property
    def tls_host(self) -> str | None:
        """The host the dev certificate names, or ``None`` when TLS is off.

        A certificate names the dev host, so ``dev_https`` without one is off.
        """
        return self.dev_host if self.dev_https else None

    @property
    def dev_scheme(self) -> str:
        """``https`` when dev envs serve TLS, else ``http``."""
        return "https" if self.tls_host else "http"

    @classmethod
    def default(cls) -> "GlobalConfig":
        """Return default global config."""
        return cls(projects_dir=Path.home() / "Projects")

    @classmethod
    def from_dict(cls, data: dict) -> "GlobalConfig":
        """Create from dictionary."""
        projects_dir = data.get("projects_dir", "~/Projects")
        open_command = data.get("open_command", "code")
        dev_host = _host_name(data.get("dev_host"))
        # Support nested linear config: linear.api_key
        linear_config = data.get("linear", {})
        linear_api_key = (
            linear_config.get("api_key") if isinstance(linear_config, dict) else None
        )
        # Support nested sentry config: sentry.api_key
        sentry_config = data.get("sentry", {})
        sentry_api_key = (
            sentry_config.get("api_key") if isinstance(sentry_config, dict) else None
        )
        # Support nested uptimerobot config: uptimerobot.api_key
        ur_config = data.get("uptimerobot", {})
        uptimerobot_api_key = (
            ur_config.get("api_key") if isinstance(ur_config, dict) else None
        )
        # Support nested openai config: openai.api_key
        openai_config = data.get("openai", {})
        openai_api_key = (
            openai_config.get("api_key") if isinstance(openai_config, dict) else None
        )
        # Support nested github config: github.orchestrator_token
        github_config = data.get("github", {})
        github_orchestrator_token = (
            github_config.get("orchestrator_token")
            if isinstance(github_config, dict)
            else None
        )
        # Support nested slack config: slack.webhooks (named map of channel -> URL)
        slack_config = data.get("slack", {})
        slack_webhooks: dict[str, str] = {}
        if isinstance(slack_config, dict):
            raw = slack_config.get("webhooks", {})
            if isinstance(raw, dict):
                # dict preserves YAML insertion order — relied on for "first = default".
                slack_webhooks = {str(k): str(v) for k, v in raw.items()}
        return cls(
            projects_dir=Path(projects_dir).expanduser(),
            open_command=open_command,
            dev_host=dev_host,
            dev_https=data.get("dev_https") is True,
            linear_api_key=linear_api_key,
            sentry_api_key=sentry_api_key,
            uptimerobot_api_key=uptimerobot_api_key,
            openai_api_key=openai_api_key,
            github_orchestrator_token=github_orchestrator_token,
            slack_webhooks=slack_webhooks,
        )


@dataclass
class ResolvedContext:
    """Resolved project and worktree context."""

    projects_dir: Path
    project: str | None
    worktree: str | None

    @property
    def project_path(self) -> Path | None:
        """Full path to project directory."""
        if self.project:
            return self.projects_dir / self.project
        return None

    @property
    def worktree_path(self) -> Path | None:
        """Full path to worktree directory."""
        if self.project and self.worktree:
            from .worktree_model import get_worktree_folder_name

            folder_name = get_worktree_folder_name(self.project, self.worktree)
            return self.projects_dir / self.project / folder_name
        return None


def load_global_config() -> GlobalConfig:
    """Load global config from ~/.maelstrom/config.yaml (or legacy ~/.maelstrom.yaml).

    Returns:
        GlobalConfig with projects_dir setting, or defaults if file doesn't exist.
    """
    # Loading is a pure read: it never tightens permissions. The config holds
    # plaintext API keys, so loose perms are a real risk — but fixing them lives
    # in ``mael doctor`` (``_check_secret_file_perms``) and ``mael self-update``
    # (which calls :func:`harden_global_config`), not on this hot read path that
    # runs on nearly every command.

    # Try new location first
    new_config_path = get_maelstrom_dir() / GLOBAL_CONFIG_FILENAME
    if new_config_path.exists():
        try:
            with open(new_config_path) as f:
                data = yaml.safe_load(f) or {}
            return GlobalConfig.from_dict(data)
        except (yaml.YAMLError, OSError):
            return GlobalConfig.default()

    # Fall back to legacy location
    legacy_config_path = Path.home() / GLOBAL_CONFIG_FILENAME_LEGACY
    if legacy_config_path.exists():
        try:
            with open(legacy_config_path) as f:
                data = yaml.safe_load(f) or {}
            return GlobalConfig.from_dict(data)
        except (yaml.YAMLError, OSError):
            return GlobalConfig.default()

    return GlobalConfig.default()


def harden_global_config() -> list[str]:
    """Tighten the global config file(s) and ~/.maelstrom dir to 0o600/0o700.

    The config holds plaintext API keys; maelstrom never writes it (users create
    it by hand), so this is the explicit, opt-in hardening path — called from
    ``mael self-update`` (and mirrored by the ``mael doctor`` check). It is *not*
    run on the config load path, which stays a pure read.

    Tightens both the new-location ``~/.maelstrom/config.yaml`` (plus its parent
    dir) and the legacy ``~/.maelstrom.yaml``; the legacy file lives directly
    under ``$HOME``, whose mode we must never touch. Best-effort and narrow-only
    (see :func:`mael_common.util.harden_path`): existing tighter perms are left
    alone, and any ``OSError`` is swallowed so the caller never crashes.

    Returns:
        Human-readable messages for each path that was tightened (empty if
        nothing was loose).
    """
    messages: list[str] = []
    maelstrom_dir = get_maelstrom_dir()

    targets: list[tuple[Path, int, str]] = [
        (maelstrom_dir, 0o700, "~/.maelstrom"),
        (maelstrom_dir / GLOBAL_CONFIG_FILENAME, 0o600, "~/.maelstrom/config.yaml"),
        (Path.home() / GLOBAL_CONFIG_FILENAME_LEGACY, 0o600, "~/.maelstrom.yaml"),
        # Each spawn record holds the env its agent was started with.
        (maelstrom_dir / "agents", 0o700, "~/.maelstrom/agents"),
    ]
    for path, mode, label in targets:
        if not path.exists():
            continue
        try:
            if harden_path(path, mode):
                messages.append(f"tightened permissions on {label}")
        except OSError:
            pass
    return messages


# Names a project may not take, because something else already owns that key
# space in the shared task store (``~/.maelstrom/tasks``). A task key is
# ``<project>/<status>/<id>.md``, so a project named ``_wiki`` would write its
# tasks into the wiki's key space and its tasks would list as wiki pages.
# Enforcing the name here is what makes :data:`mael_domain.wiki.WIKI_PREFIX` a real
# guarantee rather than a convention — the leading underscore only makes the
# collision unlikely, not impossible.
RESERVED_PROJECT_NAMES = frozenset({"_wiki"})


def validate_project_name(name: str) -> None:
    """Validate that a project name is valid.

    Args:
        name: The project name to validate.

    Raises:
        ValueError: If name is empty, contains dots, or is a reserved name.
    """
    if not name:
        raise ValueError("Project name cannot be empty")
    if "." in name:
        raise ValueError(
            f"Invalid project name '{name}': project names cannot contain dots"
        )
    if name in RESERVED_PROJECT_NAMES:
        raise ValueError(
            f"Invalid project name '{name}': that name is reserved by maelstrom"
        )


def parse_target_arg(arg: str | None) -> tuple[str | None, str | None]:
    """Parse a project.worktree argument.

    Args:
        arg: The argument string, which can be:
            - None or "" -> (None, None)
            - "project.worktree" -> (project, worktree)
            - "project" (no dot) -> (project, None)

    Returns:
        Tuple of (explicit_project, explicit_worktree).

    Raises:
        ValueError: If argument format is invalid (e.g., starts with dot).
    """
    if not arg:
        return (None, None)

    if arg.startswith("."):
        raise ValueError(f"Invalid argument '{arg}': cannot start with a dot")

    if "." in arg:
        # Split on first dot only
        dot_index = arg.index(".")
        project = arg[:dot_index]
        worktree = arg[dot_index + 1 :]

        if not project:
            raise ValueError(f"Invalid argument '{arg}': project name cannot be empty")
        if not worktree:
            raise ValueError(f"Invalid argument '{arg}': worktree name cannot be empty")

        validate_project_name(project)

        # Resolve single-letter shortcodes (e.g., "proj.a" -> "proj.alpha")
        from .worktree_model import resolve_worktree_shortcode

        worktree = resolve_worktree_shortcode(worktree)

        return (project, worktree)

    # No dot - this is just a project or worktree name (determined by context)
    # Resolve single-letter shortcodes (e.g., "a" -> "alpha")
    from .worktree_model import resolve_worktree_shortcode

    return (resolve_worktree_shortcode(arg), None)


def detect_context_from_cwd(
    projects_dir: Path,
    cwd: Path | None = None,
) -> tuple[str | None, str | None]:
    """Detect project and worktree from current working directory.

    Args:
        projects_dir: The configured projects directory.
        cwd: Current working directory (default: Path.cwd()).

    Returns:
        Tuple of (project_name, worktree_name). Either or both may be None
        if not detectable from cwd.

    The detection works by checking if cwd is under projects_dir:
    - <projects_dir>/<project>/ -> (project, None)
    - <projects_dir>/<project>/<worktree>/ -> (project, worktree)
    - <projects_dir>/<project>/<worktree>/subdir/ -> (project, worktree)
    """
    if cwd is None:
        cwd = Path.cwd()

    cwd = cwd.resolve()
    projects_dir = projects_dir.resolve()

    # Check if cwd is under projects_dir
    try:
        relative = cwd.relative_to(projects_dir)
    except ValueError:
        # cwd is not under projects_dir
        return (None, None)

    parts = relative.parts
    if not parts:
        # cwd is exactly projects_dir
        return (None, None)

    project = parts[0]

    if len(parts) < 2:
        # cwd is at project level
        return (project, None)

    folder_name = parts[1]

    # Try to extract worktree name from folder (handles "project-alpha" format)
    from .worktree_model import WORKTREE_NAMES, extract_worktree_name_from_folder

    worktree = extract_worktree_name_from_folder(project, folder_name)

    # Fall back to checking if folder_name itself is a valid worktree name
    # (for backwards compatibility with old format)
    if worktree is None and folder_name in WORKTREE_NAMES:
        worktree = folder_name

    return (project, worktree)


def resolve_context(
    arg: str | None,
    require_project: bool = False,
    require_worktree: bool = False,
    cwd: Path | None = None,
    arg_is_project: bool = False,
) -> ResolvedContext:
    """Resolve project and worktree from argument and/or cwd context.

    This is the main entry point for argument resolution. It:
    1. Loads global config to get projects_dir
    2. Parses the explicit argument
    3. Detects context from cwd if needed
    4. Merges explicit and detected values (explicit takes precedence)
    5. Validates requirements

    Args:
        arg: The target argument (project.worktree format).
        require_project: If True, error if project cannot be determined.
        require_worktree: If True, error if worktree cannot be determined.
        cwd: Current working directory (default: Path.cwd()).
        arg_is_project: If True, treat a single-name arg as a project name
            even when inside a project directory (skips worktree reinterpretation).

    Returns:
        ResolvedContext with project and worktree information.

    Raises:
        ValueError: If requirements not met or validation fails.
    """
    global_config = load_global_config()
    projects_dir = global_config.projects_dir

    # Parse the explicit argument
    explicit_project, explicit_worktree = parse_target_arg(arg)

    # Detect context from cwd
    detected_project, detected_worktree = detect_context_from_cwd(projects_dir, cwd)

    # If arg has no dot, it could be:
    # - A project name (if we're not in a project dir, or arg_is_project=True)
    # - A worktree name (if we're in a project dir and arg_is_project=False)
    # We interpret based on context
    if explicit_project is not None and explicit_worktree is None:
        # Arg was a single name without dot
        if detected_project is not None and not arg_is_project:
            # We're in a project dir, so arg is the worktree
            explicit_worktree = explicit_project
            explicit_project = None

    # Merge explicit and detected values (explicit takes precedence)
    project = explicit_project if explicit_project is not None else detected_project
    worktree = explicit_worktree if explicit_worktree is not None else detected_worktree

    # Validate requirements
    if require_project and project is None:
        raise ValueError(
            "Could not determine project. Specify as 'project.worktree' "
            "or run from within a project directory."
        )

    if require_worktree and worktree is None:
        raise ValueError(
            "Could not determine worktree. Specify as 'project.worktree' "
            "or run from within a worktree directory."
        )

    return ResolvedContext(
        projects_dir=projects_dir,
        project=project,
        worktree=worktree,
    )


def resolve_project(project: str | None) -> str:
    """Return the project name, defaulting to the cwd's project."""
    if project:
        return project
    ctx = resolve_context(None, require_project=True)
    assert ctx.project is not None  # require_project guarantees this
    return ctx.project
