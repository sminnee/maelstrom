"""Context resolution for maelstrom commands.

This module handles resolving project and worktree context from:
- Explicit command-line arguments (project.worktree format)
- Current working directory detection
- Global configuration (~/.maelstrom.yaml)
"""

from dataclasses import dataclass
from pathlib import Path

import yaml


GLOBAL_CONFIG_FILENAME = ".maelstrom.yaml"


@dataclass
class GlobalConfig:
    """Global maelstrom configuration from ~/.maelstrom.yaml."""

    projects_dir: Path
    open_command: str = "code"
    linear_api_key: str | None = None

    @classmethod
    def default(cls) -> "GlobalConfig":
        """Return default global config."""
        return cls(projects_dir=Path.home() / "Projects")

    @classmethod
    def from_dict(cls, data: dict) -> "GlobalConfig":
        """Create from dictionary."""
        projects_dir = data.get("projects_dir", "~/Projects")
        open_command = data.get("open_command", "code")
        # Support nested linear config: linear.api_key
        linear_config = data.get("linear", {})
        linear_api_key = linear_config.get("api_key") if isinstance(linear_config, dict) else None
        return cls(
            projects_dir=Path(projects_dir).expanduser(),
            open_command=open_command,
            linear_api_key=linear_api_key,
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
            from .worktree import get_worktree_folder_name
            folder_name = get_worktree_folder_name(self.project, self.worktree)
            return self.projects_dir / self.project / folder_name
        return None


def load_global_config() -> GlobalConfig:
    """Load global config from ~/.maelstrom.yaml.

    Returns:
        GlobalConfig with projects_dir setting, or defaults if file doesn't exist.
    """
    config_path = Path.home() / GLOBAL_CONFIG_FILENAME
    if not config_path.exists():
        return GlobalConfig.default()

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        return GlobalConfig.from_dict(data)
    except (yaml.YAMLError, OSError):
        return GlobalConfig.default()


def validate_project_name(name: str) -> None:
    """Validate that a project name is valid.

    Args:
        name: The project name to validate.

    Raises:
        ValueError: If name is empty or contains dots.
    """
    if not name:
        raise ValueError("Project name cannot be empty")
    if "." in name:
        raise ValueError(
            f"Invalid project name '{name}': project names cannot contain dots"
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
        raise ValueError(
            f"Invalid argument '{arg}': cannot start with a dot"
        )

    if "." in arg:
        # Split on first dot only
        dot_index = arg.index(".")
        project = arg[:dot_index]
        worktree = arg[dot_index + 1:]

        if not project:
            raise ValueError(
                f"Invalid argument '{arg}': project name cannot be empty"
            )
        if not worktree:
            raise ValueError(
                f"Invalid argument '{arg}': worktree name cannot be empty"
            )

        validate_project_name(project)
        return (project, worktree)

    # No dot - this is just a project or worktree name (determined by context)
    return (arg, None)


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
    from .worktree import extract_worktree_name_from_folder, WORKTREE_NAMES
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
