# Maelstrom installs as a release bundle

Status: accepted, 2026-11-05.

## Context

Maelstrom ran from the `_main` worktree of the maelstrom project. `mael self-update` ran `git
pull` there and reinstalled the CLI as an editable uv tool. The web UI was the Vite dev server.
Only the CLI wheel went to PyPI, and that install had no daemon, no orchestrator and no UI.

So an agent's work on the maelstrom project could change what ran, and a second host could not
install maelstrom at all.

## Decision

A release is a **release bundle**: one tarball, `maelstrom.tar.gz`, on a GitHub Release. It holds
a `manifest.toml`, the installer `install.py`, and one directory per **component**.

### The bundle

- The release bundle is the unit, not a Python package. A wheel is a detail of some components.
  The UI is static files.
- The manifest names a **bundle kind** and its components. Today the one kind is `host`.
- A component's version is the hash of its SHA bucket in `pipeline.toml`. It moves only when a
  commit changes that component's files. The release tag, such as `v0.3.0`, names the bundle.
  It does not name the components.
- The `host` components are `cli`, `orchestrator`, `agent-daemon`, `web`, `common`, `agent`,
  `domain` and `python-deps`. The jig is not one: the built UI does not load it.
- A bundle carries every component, not only the ones that moved. That is simpler, and the
  tarball is a few MB. The installer skips the components it already has.

### The install

- `~/.maelstrom/install/components/<name>/<version>/` holds each component.
  `releases/<tag>/manifest.toml` names the versions of one release. A `current` symlink selects
  the release. A switch is one atomic rename, and a rollback is one more.
- Each release gets its own venv, `releases/<tag>/venv`, built with uv from the bundled wheels
  and the pinned requirements. uv is a host prerequisite.
- The installer is one stdlib-only module, so it runs from the bundle before any venv exists.
- The orchestrator serves the `web` component beside its API, on one origin. Dev worktrees keep
  the Vite dev server.
- The install is apart from every checkout. The maelstrom project becomes an ordinary project.

### Releases and updates

- GitHub Releases is the only channel. PyPI is dropped.
- CI makes the release: `release.yml` builds and publishes the bundle on a `v*` tag.
  `bin/publish` retitles the CHANGELOG and pushes the tag.
- `mael self-update` has two channels and one install path. `stable` downloads the latest GitHub
  Release. `main` builds a bundle from `_main` with `bin/build-bundle`, the script CI uses.

### Later tasks

These are decided here and built after the bundle:

- The OS supervises the services: systemd user units with linger on Linux, launchd agents with
  `KeepAlive` on macOS. The `/bin/sh` shim stays for interactive `mael`.
- A systemd timer with `Persistent=true` runs the scheduler, so a run missed while the host is
  down fires later.
- Linux has no cmux. Without cmux, a session runs in the agent daemon with no attach pane.
- `mael setup` checks for the host tools and offers to install each missing one.
- `mael migrate` moves a host's notebook and project list to a new machine.
- A later `satellite` bundle kind uses the same format and installer.

## Consequences

- A component whose version did not move is not copied again, and an update reports which
  components changed.
- `shared/` moves into the `mael-domain` wheel, because the orchestrator reads it as well as the
  CLI. A skill edit now moves the orchestrator's version too.
- The CLI wheel no longer copies the libraries into itself. Nothing installs it alone.
- Old releases and component versions stay on disk as rollback targets. Nothing prunes them yet.
