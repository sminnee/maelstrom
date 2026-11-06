# `mael self check-host` provisions a fresh host

Status: accepted, 2026-11-06. Adds a sixth `mael self` command to
[ADR 0002](0002-one-installer-and-host-services.md)'s five, and changes where that ADR put the
production config.

## Context

After ADR 0002, one `curl … | sh` line installed the release and ran `mael self setup`. The
installer stopped when uv was missing. Nothing checked or installed the other tools. Nothing set
`projects_dir:`, and a fresh install wrote no `~/.maelstrom/config.yaml`. The user signed in to
`gh`, `claude`, `codex` and Tailscale by hand, from a guide that covered macOS only.

## Decision

- **`mael self check-host` is its own command, not a step of setup.** `install.sh` runs setup on
  every update, and an update must not prompt. A tool the user declined would be asked about on
  each update.
- **`install.sh` runs it on a first install only**: when there was no `current/manifest.toml`
  before the install. The user runs it again by hand. The installer ignores its exit code,
  because the host services start without `gh` or `codex`.
- **It runs before setup.** Setup binds the orchestrator from `dev_host:` and
  `dev_https:`, and the check sets those keys.
- **It asks before each change.** For a missing tool, it prints the `brew` or `apt-get` command,
  and runs it after a yes. Without a tty, it asks nothing and only reports.
- **A release install needs no Node and no pnpm.** The bundle carries the built web UI, and Claude
  Code has a native installer. `--dev` adds them for a source install. Codex installs through
  npm, so a yes to Codex installs Node first.
- **Node on Ubuntu comes from the NodeSource 22 repository.** apt's own `nodejs` is v18. nvm sets
  PATH in `.bashrc`, which a systemd unit and a non-interactive shell do not read.
- **`install.sh` installs uv itself**, with uv's own installer.
- **Setup asks for `projects_dir:`** when it is unset and a tty can answer.
- **The production config lives in `~/.maelstrom/config.yaml`.** `orchestrator_url:` and
  `service_path:` move up from `~/.maelstrom/state/config.yaml`, so the user edits one file.
  The production notebook root holds only the notebook, and no config of its own is read there. A
  playpen notebook root still reads `orchestrator_url:` only from its own `config.yaml`, so a dev
  environment never reaches the real server.
- **`mael doctor` becomes `mael project check`**, and keeps only the project checks. The checks
  of `~/.maelstrom` move to `check-host`.

## Consequences

- One line takes a fresh macOS or Ubuntu Server 24.04 host to running host services.
- The install ends with the orchestrator URL, from setup's last line.
- A `dev_host:` set after the first setup takes effect at the next setup. As first written, setup
  wrote `orchestrator_url:` once and the key then drifted from `dev_host:`. Production now derives
  the URL from `dev_host:` and `dev_https:`, and setup deletes a stored key.
- cmux is not on the checklist. It is macOS only.
