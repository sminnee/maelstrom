"""Integration with Claude Code for skills and hooks."""

import shutil
from pathlib import Path


def get_shared_dir() -> Path:
    """Get path to maelstrom's shared/ directory."""
    module_dir = Path(__file__).parent
    dev_path = module_dir.parent.parent / "shared"
    if dev_path.exists():
        return dev_path
    raise FileNotFoundError("Could not locate maelstrom shared directory")


def _symlink_items(source_dir: Path, target_dir: Path) -> list[str]:
    """Symlink all items from source_dir into target_dir. Returns messages."""
    messages = []
    if not source_dir.exists():
        return [f"Source not found: {source_dir}"]

    target_dir.mkdir(parents=True, exist_ok=True)

    for item in source_dir.iterdir():
        target = target_dir / item.name

        if target.is_symlink():
            if target.resolve() == item.resolve():
                continue  # Already correctly linked
            old_target = target.resolve()
            target.unlink()
            messages.append(f"Replaced old link {target.name} (was {old_target})")
        elif target.exists():
            backup = target.with_suffix(".backup")
            if backup.exists():
                shutil.rmtree(backup)
            target.rename(backup)
            messages.append(f"Backed up existing {target.name}")

        target.symlink_to(item)
        messages.append(f"Linked {target} -> {item}")

    return messages


def install_claude_integration() -> list[str]:
    """Install skills and hooks by symlinking to ~/.claude/."""
    shared = get_shared_dir()
    claude_dir = Path.home() / ".claude"

    messages = []

    # Symlink skills
    skills_source = shared / "skills"
    if skills_source.exists():
        messages.extend(_symlink_items(skills_source, claude_dir / "skills"))

    # Symlink hooks
    hooks_source = shared / "hooks"
    if hooks_source.exists():
        messages.extend(_symlink_items(hooks_source, claude_dir / "hooks"))

    # Symlink commands
    commands_source = shared / "commands"
    if commands_source.exists():
        messages.extend(_symlink_items(commands_source, claude_dir / "commands"))

    return messages or ["Nothing to install"]
