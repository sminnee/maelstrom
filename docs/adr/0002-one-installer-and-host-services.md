# One installer, and the OS supervises the host services

Status: accepted, 2026-11-06. Changes two decisions of
[ADR 0001](0001-maelstrom-installs-as-a-release-bundle.md) and builds two of its later tasks.

## Context

ADR 0001 made maelstrom a **release bundle** that `install.py` installs. A host still needed
several more commands after that: `mael install` for the Claude integration, `mael schedule
install`, a manual `state.db` migration, and `mael self-env start` to run the orchestrator and the
agent daemon from `_main` with `uv run`. Nothing supervised those two processes. A crash or a
reboot left them down, and the `stable` channel did not restart them after an update.

`install.py` also needed a Python on the host before any venv existed.

## Decision

### One installer: `install.sh`

- `bin/install.sh` replaces `install.py`. It is a POSIX shell script. Its prerequisites are `curl`,
  `tar` and `uv`. uv fetches Python 3.13 for the venv, so the host needs no Python of its own.
- `curl -LsSf https://github.com/sminnee/maelstrom/releases/latest/download/install.sh | sh`
  installs or upgrades a host. `release.yml` publishes `install.sh` beside the tarball, and
  `bin/build-bundle` copies it into the bundle.
- `--tag` installs one release, `--bundle` a local tarball, and `--source DIR` a bundle built from
  a git checkout, tagged `main-<sha12>`.
- The script keeps every rule of `install.py`.
  [host-services.md](../dev/host-services.md#the-installer) lists them.
- It reads `manifest.toml` with `awk`, because the format is ours and fixed. `Manifest` and
  `installed_web_root` stay in Python, in `mael_domain/release_bundle/layout.py`, because the
  build, `mael self status` and the orchestrator use them.

### One setup step: `mael self setup`

- After the switch, `install.sh` runs the new release's `mael self setup --changed <components>`.
  So setup is always the code of the release it sets up.
- Setup applies every step on every run, and each step is safe to repeat. A changed
  `orchestrator_url` or `dev_https` takes effect on the next update with no other command.
- The migration comes before any restart. The orchestrator refuses an unmigrated `state.db`, and
  under `KeepAlive` that refusal is a crash loop.

### Host services

- The orchestrator and the agent daemon are **host services**: launchd agents with `KeepAlive` on
  macOS, systemd user units with `Restart=always` and linger on Linux.
- The scheduler is a unit of the same supervisor: an hourly launchd agent, or a systemd timer with
  `Persistent=true`. It is always on. The `schedule.enabled` opt-in marker is gone.
- A changed `orchestrator` or `web` component restarts the orchestrator. A changed
  `agent-daemon` restarts the daemon. A SHA bucket holds its `depends`, so a library change moves
  those components too.

### The production state

- The production notebook root is `~/.maelstrom/state`, and the production daemon root is
  `~/.maelstrom/state/daemon`. Before, the notebook root was `~/.maelstrom` itself, mixed with
  logs and ports, and the daemon root was named after `_main`, which no longer runs it.
- `orchestrator_url:` moves to `~/.maelstrom/state/config.yaml`, because a notebook root's config
  is where clients read it. Setup writes the key when it is unset, so that a client never calls
  `tailscale` on each command.

### The command surface

- `mael self-update` becomes `mael self update`. `mael self` has five commands: `setup`, `update`,
  `status`, `uninstall` and `restart`.
- `mael install`, `mael schedule`, `mael admin` and `mael self-env` are removed with no stubs.
- `cli/src/mael_cli/self/` is fenced by import-linter: it imports only the libraries, and only
  `mael_cli.cli` imports it. A later change can then make it a binary of its own.

## Consequences

- A host installs and upgrades with one command, and a crash or a reboot no longer leaves the
  orchestrator or the daemon down.
- The first setup on an older host moves its notebook and its daemon root into `state/`, once.
- An update from an agent session of the production daemon stops that agent when the daemon restarts.
  Run `mael self update` from a shell.
- A host-service orchestrator does not renew the dev certificate. Only `mael env start` does.
- A first install from source still needs a release, or `bin/build-bundle` and `install.sh
  --bundle`, before `mael project add` works.

  _2026-11-06:_ no longer true. `bin/install-main.sh` clones `_main` with git alone, then runs
  its `install.sh --source`. See [host services](../dev/host-services.md#the-bootstrap).
