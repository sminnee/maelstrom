"""Stub file contents for a new maelstrom project.

Pure content generation — no filesystem access. :func:`create_project_repo` in
``github.py`` writes these into the seed commit.
"""

GITIGNORE = """\
# Maelstrom generates these per worktree. Do not commit them.
.env
.claude/CLAUDE.local.md
"""

MAELSTROM_YAML = """\
# Maelstrom project configuration. Every key is optional.
# See docs/reference/configuration.md in the maelstrom repository.

# install_cmd: "npm install"

# services:
#   web:
#     ports: [FRONTEND]
#     command: npm run dev -- --port ${FRONTEND_PORT}

# start_cmd: "npm run dev"

# linear:
#   team_id: "TEAM"
#   product_label: "product"
"""


def scaffold_files(name: str) -> dict[str, str]:
    """Return ``{filename: content}`` for a new project's initial commit.

    ``CLAUDE.md`` starts with the ``@.claude/CLAUDE.local.md`` import because
    ``_ensure_claude_md_import`` does nothing when ``CLAUDE.md`` is absent. The
    import dangles until the first ``mael add`` writes the local file. Claude
    accepts a missing @-import.
    """
    return {
        ".gitignore": GITIGNORE,
        ".maelstrom.yaml": MAELSTROM_YAML,
        "README.md": f"# {name}\n\nA maelstrom-managed project.\n",
        "CLAUDE.md": f"@.claude/CLAUDE.local.md\n\n# {name}\n",
    }
