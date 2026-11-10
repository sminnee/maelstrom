# Host services

How a host installs maelstrom, and how the OS keeps its **host services** running. The decisions
are in [ADR 0002](../adr/0002-one-installer-and-host-services.md) and
[ADR 0003](../adr/0003-check-host-provisions-a-fresh-host.md).

A [host service](../../CONTEXT.md) is `orchestrator` or `agent-daemon`, run by the OS supervisor.
It is not a **Service** of a worktree.

## The flow

```
curl …/install-main.sh | sh ──▶ clone <projects_dir>/maelstrom/_main ──▶ install.sh --source …
                                                                              │
curl … | sh  ─┐                                                               ▼
mael self update ─┼─▶ install.sh ──▶ projects dir ──▶ components, venv, `current`
sh install.sh --source DIR ─┘                                                 │
                                                                              ▼
                                         mael self check-host --quiet (each install;
                                         --source runs it before the build)
                                                                              │
                                                                              ▼
                                                     mael self setup --changed …
                                                     (the new release's code)
```

1. `bin/install.sh` installs uv when it is missing.
2. It asks for the projects directory when `projects_dir:` is unset, and writes the key.
3. It places the release under `~/.maelstrom/install/` and moves `current`.
4. It runs `current/venv/bin/mael self check-host --quiet`. With `--source`, it runs
   `check-host --dev --quiet` from the checkout before the build instead.
5. It runs `current/venv/bin/mael self setup --changed <components>`.
6. Setup brings the host to the state the release wants.

`mael self update` runs `install.sh` for the stored channel. `--detach` runs it in the
[update job](#the-update-job). See
[the CLI reference](../reference/cli.md#host-control-mael-self).

## The installer

`bin/install.sh` is POSIX `sh`. Its prerequisites are `curl` and `tar`. It installs uv with uv's
own installer when uv is not on PATH, and stops with that command when the install fails. uv
fetches Python 3.13 for the venv. The whole script is one `main` function, so `curl | sh` runs nothing until the
script has arrived.

| Rule | Why |
|---|---|
| Each component version is copied once, through a temp directory and a rename | A second release shares the unchanged versions, and a failed copy leaves no half directory |
| A release with the same versions keeps its venv | A reinstall costs nothing |
| A running tag with other versions is refused | Its venv runs now, and a rebuild under it would change what runs |
| A half-built venv is removed | It has a `python`, so the next run would take it for a finished one |
| `current` moves last, with `ln -s` and `mv -h` (macOS) or `mv -T` (Linux) | A failure before the switch leaves the release that runs as it was |
| A tarball member outside `maelstrom/`, or with a `..`, is refused | The bundle unpacks under the install root, and nothing may land beside it |
| The changed components are added to `pending-changed` in the install root before setup runs | Setup deletes the record once the restarts succeed, so a setup that stops early leaves them to the next |
| The projects directory is resolved before the download or the build, also with `--no-setup` | The question comes first, and setup then finds the key set |
| The directory is asked for only when `--projects-dir` is not given and `projects_dir:` is unset | `mael self update` cannot pass the flag, because an older release's `install.sh` refuses unknown arguments |
| `check-host --quiet` runs on each install | A run that failed after the switch made the rerun look like an update, and the check never ran. `--quiet` keeps an update on a met host silent |
| With `--source`, `check-host --dev` runs before the build, through `uv run --project DIR`, and not again after the switch | `bin/install` and `bin/build-bundle` need `node` and `pnpm`, and the checkout's `mael` needs only uv |
| With `--source`, the script installs tangier with `uv tool install` when it is missing | The build hashes each component with `tangier changemap sha`, and uv is on PATH already |
| With `--source`, the script stops before `bin/install` when `node` or `pnpm` is missing | The build needs both, and `check-host`'s exit code is ignored |
| `check-host`'s exit code is ignored | The host services start without `gh` or `codex` |
| `check-host` and setup read `/dev/tty` when it opens | Under `curl \| sh`, stdin is the script |

The script reads `manifest.toml` with `awk`. The format is fixed and flat: one `key = value` per
line, and one `[components.<name>]` table per component. `bin/build-bundle` writes it through
`Manifest` in `mael_domain/release_bundle/layout.py`.

`COMPONENTS` is in the script and in `layout.py`. Keep the two lists the same.

With `--source DIR`, the script runs `git pull --ff-only` in `DIR`. Then it runs the checkout's
own `install.sh`, because the pull can change it. It installs tangier when it is missing, and
stops when `node` or `pnpm` is not on PATH. Then it runs `bin/install --no-migrate` with `MAEL_NOTEBOOK_ROOT` unset, so that only
setup migrates the production state database. Then
`bin/build-bundle` makes a bundle tagged `main-<sha12>`. A tag that runs now is not built again.

### The bootstrap

`bin/install-main.sh` installs from main on a host with no release and no checkout. It does the
git steps of `_lay_out_project()` for `_main` alone, writes the `.mael` marker, and writes
`update_channel: main`. Then it runs `_main`'s own `install.sh --source --projects-dir`, so all
install logic stays in one script. A layout step that fails, or an interrupt, removes the project directory. It writes
`update_channel: main` after the layout, so a failed clone leaves the channel as it was. It
carries a copy of `install.sh`'s projects-directory functions, because it
runs before any checkout exists. Keep the two copies the same.

## Setup

`mael self setup` is in `cli/src/mael_cli/self/setup.py`. Each step is safe to repeat, so every
run applies all of them. `--changed`, with the components in `pending-changed`, decides what
restarts. A unit whose file changed also reloads. A host service that does not load or restart
fails the setup.

| Step | What it does |
|---|---|
| Global config | Makes sure `~/.maelstrom/config.yaml` holds `projects_dir:`, and creates the directory. The installer writes the key, so this asks only on a host whose key was removed after the install, when stdin is a tty |
| Relocate | Once: moves `state.db`, `tasks/`, `desk.json` and `daemons/_main/` from an older layout into `~/.maelstrom/state/` |
| Migrate | Migrates `state/state.db`, moves `service_path:` up from an older `state/config.yaml` and writes it when it is unset, and deletes a stored `orchestrator_url:`. Once, the migration imports the wiki from an old `tasks/` git notebook and moves its media into `state/attachments/` |
| Integrate | The Claude sandbox exclusions, then the plugins (see [Skills and plugins](#skills-and-plugins)), then `harden_global_config` |
| Shim | Writes `~/.local/bin/mael` |
| Jig | With `--source` only: builds the checkout's jig plugin and runs `pnpm link --global` |
| Units | Stops `_main`'s env-managed orchestrator and daemon, then writes and loads the host service units |
| Restart | Restarts the host services whose components changed, and that the load did not just start |
| Scheduler | Writes and loads the hourly scheduler unit |

**The migration comes before the restarts.** The orchestrator refuses an unmigrated `state.db`.
Under `KeepAlive`, that refusal is a crash loop.

**The units come after `_main` stops.** The env-managed daemon holds the flock on the daemon
root, and the env-managed orchestrator holds the port. A host service started first fails on
both.

### The orchestrator URL

The production URL is derived, not stored: `GlobalConfig.derived_orchestrator_url` builds it
from `dev_host:` and `dev_https:`, on `PRODUCTION_ORCHESTRATOR_PORT` (2772). For the production
root, `~/.maelstrom/state`, `load_config_at()` reads `~/.maelstrom/config.yaml` alone, ignores an
`orchestrator_url:` in it, and sets the derived URL. For every other notebook root it drops the
global key, so a dev environment never reaches the real server. Setup deletes a stored
`orchestrator_url:`, which an older setup wrote. When the stored value differs from the derived
one, setup prints both.

One pair of keys drives both the clients and the unit, so the two cannot drift. Each setup
renders the orchestrator unit from the same config:

- `--host 127.0.0.1`: plain HTTP on loopback, always.
- `--public-host <dev_host:>`, when `dev_host:` is set: a second listener on the addresses the
  name resolves to.
- `DEV_TLS_CERT` and `DEV_TLS_KEY` under `dev_https:`. They apply to the public listener only.

Under `dev_https:`, setup also runs `tailscale cert`, which renews the dev certificate when fewer
than 30 days remain. A changed certificate restarts the orchestrator, which reads it only at
start. A failed issue is a warning, and setup goes on. The last line of setup names the URL.

### The production environment

The units and the shim share `production_env()` in `production.py`:

| Variable | Value |
|---|---|
| `MAEL_NOTEBOOK_ROOT` | `~/.maelstrom/state` |
| `MAEL_AGENT_ROOT` | `~/.maelstrom/state/daemon` |
| `MAEL_PRODUCTION` | `1` |
| `PATH` | `service_path:` from `~/.maelstrom/config.yaml` |

The first setup writes `service_path:`: `~/.local/bin`, then the PATH setup ran with, less any
venv `bin`, then the system directories. The daemon's agents need `claude`, `git`, `uv` and the
other tools of the user's shell. A venv `bin` is dropped, so a worktree's `mael` never shadows the
shim. Later setups reuse the stored value, so a setup from another shell writes the same unit
files and restarts nothing. Edit the key to change the units' PATH. The shim keeps the caller's
PATH, and sets a root only when none is set.

## Host check

`mael self check-host` is in `cli/src/mael_cli/self/host_check.py`. The check is one list,
`HOST_ITEMS`. Each entry is a `HostCheckItem`: `applies()` says whether it counts on this host,
`check()` returns a `Finding` (met, and the line that says so), and `remedy()` returns a `Remedy`
(the hint, and a fix to run after a yes). `HostCheck` holds every outside effect as a field, as
`Setup` does, and works through the list. A met item prints one `✓` line and asks nothing. After
a fix, the item is checked again. So every run is safe to repeat. A new check is a new subclass
in the list.

| Items | What they do |
|---|---|
| `PackageManager` | `brew` on macOS, offering the Homebrew installer. `apt-get` on Linux |
| `Tool` × `HOST_TOOLS` | In install order. For a missing tool, prints the platform command, and runs it after a yes. A `brew` command waits for Homebrew. The last rows come from `LANGUAGE_SERVERS`: one per **Language server** with an install command |
| `SecretFiles` | Tightens `~/.maelstrom`, `config.yaml`, the port allocations and the spawn records of both daemon roots |
| `SignIn` × 3 | `gh auth status`, `claude auth status`, `codex login status`. Offers each login |
| `GitIdentity` | Required. A global `user.name` and `user.email`. The fix asks for each, with the `gh api user` values as defaults |
| `GitCredentials` | When gh is signed in: git's credential helper for `https://github.com` is gh's. Offers `gh auth setup-git`. A gh sign-in does not reach git by itself, and an HTTPS clone with no terminal then fails. Another helper is unmet too: macOS's `osxkeychain` default may hold nothing |
| `DockerRunning` | With `docker` on the host: `docker info` answers. Offers `open -a Docker`, `systemctl enable --now docker`, or, for a refused socket, `usermod -aG docker` |
| `PracticesPlugin` | With `claude` on the host: offers to install `minnstack` into Claude Code, and into Codex when it is there |
| `AgentBrowserSkill` | With `agent-browser` on the host: its skill in `~/.agents/skills` and `~/.claude/skills`, installed with the `skills` CLI |
| `ChromeUserns` | On Linux with `agent-browser`, when `kernel.apparmor_restrict_unprivileged_userns` is `1`: an AppArmor profile that lets Chrome make its sandbox |
| `TailscaleUp`, `TailscaleOperator`, `DevHost`, `DevHttps` | `tailscale up`, the operator (Linux), `dev_host:`, and `dev_https:` once the certificate issues |
| `ShimOnPath` | Warns when `~/.local/bin` is not on the caller's PATH |
| `ProjectHostCheck` × `project_items()` | One per project that sets `check_host_cmd`. Runs it in `_main`. Not required. The remedy is the command's output, with no fix. `cmd_check_host` adds these items after `HOST_ITEMS`, so a project's script can use the tools above |

**The check runs before setup.** Setup renders the orchestrator unit from `dev_host:` and
`dev_https:`, and the check sets those two keys, so it must come first. A key set after setup
takes effect at the next `mael self setup`.

**Tools are probed on the host services' PATH**, `agent_path()`. The question is whether the
agents find a tool. `cmd_check_host` also puts that PATH in its own environment, so a step finds
a tool an earlier step installed, such as uv in `~/.local/bin`.

`--dev` adds `node` and `pnpm`. `install.sh --source` passes it. [ADR 0003](../adr/0003-check-host-provisions-a-fresh-host.md)
gives the Node rationale.

**`--quiet` remembers a no.** It prints only the unmet items, and their section headings. A no
to an optional item adds its `name` to `host_check_declined:` in `config.yaml`, and `--quiet`
then skips that item. A required item is always offered. A check added in a later release has
no record, so the next install offers it once. Without `--quiet`, every item is offered again,
and a yes removes the item from the list.

Without a tty, the check asks nothing. It runs only the secret-file fix, which only narrows a mode.
It exits 1 when a required tool is missing, `gh` or `claude` is signed out, or git has no author
identity. A **Project host check** never changes the exit code: a project's tool does not stop
the host services.

`mael project check` keeps the worktree `.env` permissions. The files under `~/.maelstrom` are the
host check's.

## Skills and plugins

The skills ship as two plugins in one local plugin marketplace, `shared/plugins/`:

| Plugin | Holds | How an agent gets it |
|---|---|---|
| `maelstrom`, the **core plugin** | The skills the orchestrator launches by name, and the `mael` workflow | Each maelstrom launch loads it |
| `minnstack` | The development practices: design, build, test, review, writing | The user installs it globally |

**Each launch loads the core plugin of the code that launches it.** The client passes
`core_plugin_dir()` from `mael_domain/shared_dir.py`. The daemon puts it on the `claude` argv
as `--plugin-dir`, as it does with `system_prompt_file`; the daemon cannot import `mael_domain`.
The pane launcher and the headless rebase repair add the same flag. So a worktree's dev
orchestrator runs that worktree's skills, and a skill edit can be tested before it merges. A
`--plugin-dir` copy replaces a global install of the same plugin for that session.

**Codex has no `--plugin-dir`.** Setup installs the core plugin into Codex disabled. Each
maelstrom thread start and resume enables it with a `config` override,
`CODEX_CORE_PLUGIN_CONFIG` in `mael_agent/harness_model.py`. The pane passes the same override
as `-c`. The override must be a nested table: Codex accepts a dotted key with the quoted
`"maelstrom@maelstrom"` segment and ignores it. Codex copies a plugin into its cache, so a Codex
agent runs the skills of the last setup, not of its worktree.

A plugin skill is namespaced, as `/maelstrom:plan-task`. The bare `/plan-task` still resolves,
because each skill sets `name:` and no other skill has that name. `shared/tests/test_shared_skills.py`
guards both rules.

### The setup step

`Plugins.setup()` in `cli/src/mael_cli/self/plugins.py` runs these steps:

1. **Remove the old links.** Each symlink in `~/.claude/skills/` and `~/.agents/skills/` whose
   target is in a `shared/skills/` dir goes, broken or not. These links would take the bare
   skill names. Other links and real dirs stay.
2. **Move the stable path.** `~/.maelstrom/plugins` points at this release's `shared/plugins/`,
   moved with one rename.
3. **Register the marketplace.** Claude Code gets
   `claude plugin marketplace add ~/.maelstrom/plugins` once. It keeps the path as given and reads
   the plugins in place, so each new release reaches a global install at the next session
   start. Codex gets the path written into `[marketplaces.maelstrom]` in `config.toml`, because
   `codex plugin marketplace add` stores the resolved path, which the next release moves.
4. **Refresh Codex.** `codex plugin add` runs for the core plugin and for each enabled
   `@maelstrom` plugin, to refresh the cached copy. The core plugin then goes back to
   `enabled = false`, unless the user had set it to `true`.
5. **Keep the practices.** When step 1 removed a `minnstack` skill link, setup installs
   `minnstack@maelstrom`, so an upgrading host keeps those skills.

Without `claude`, setup warns and goes on. Without `codex`, it skips the Codex steps: Codex is
optional. The `config.toml` edits use `tomlkit`, which keeps the user's keys, order and comments.

## The units

`cli/src/mael_cli/self/host_service.py` holds the `HostService` specs and the two pure renderers,
`render_plist` and `render_unit`. A `Supervisor` writes the files and drives the OS supervisor
through an injected `run`. `supervisor()` picks `Launchd` on macOS and `Systemd` elsewhere.

| Unit | launchd (`~/Library/LaunchAgents/<label>.plist`) | systemd (`~/.config/systemd/user/`) |
|---|---|---|
| orchestrator | `nz.tangerinelabs.maelstrom.orchestrator` | `maelstrom-orchestrator.service` |
| agent-daemon | `nz.tangerinelabs.maelstrom.agent-daemon` | `maelstrom-agent-daemon.service` |
| scheduler | `nz.tangerinelabs.mael_domain.schedule` | `maelstrom-schedule.service` and `.timer` |

- The host services run `current/venv/bin/mael-orchestrator serve` and
  `current/venv/bin/mael-agent-daemon serve`. `current` is a symlink, so a unit file does not
  change between releases.
- launchd: `RunAtLoad` and `KeepAlive`. The logs go to `~/.maelstrom/logs/service/<name>.log`.
- systemd: `Restart=always`, `RestartSec=5` and `WantedBy=default.target`. Setup runs
  `loginctl enable-linger $USER`, so the units run without a login. A failure there warns. The
  logs go to the journal: `journalctl --user -u maelstrom-<name>.service`.
- The agent-daemon unit alone also sets `KillMode=mixed` and `OOMPolicy=continue`
  (`HostService.daemon_owns_children`). A stop signals the daemon only, and the daemon stops its
  agents so the next daemon resumes them. An OOM kill takes one agent, not the unit. See
  [agent-daemon.md](agent-daemon.md#ending-an-agent). launchd already signals only the job's own
  process.
- The scheduler runs `mael task add-scheduled --all-projects --run` through the shim, hourly. See
  [scheduled-tasks.md](scheduled-tasks.md#firing-behaviour) for when it fires.

`apply` loads a unit whose file changed or that is not loaded, and reports what it started. A
loaded unit with the same file is left running. On macOS, `apply` waits for
a booted-out job to unload before it bootstraps it again, and retries a bootstrap that races the
teardown.

### The restart mapping

`RESTARTED_BY` maps a component to the host services it restarts:

| Component | Restarts |
|---|---|
| `orchestrator` | orchestrator |
| `web` | orchestrator, which serves it |
| `agent-daemon` | agent-daemon |

A component's SHA bucket holds its `depends`. A change in `common`, `agent`, `domain` or
`python-deps` therefore moves `orchestrator` or `agent-daemon` too. On a first install every
component has changed, so both host services start.

## The update job

`mael self setup` restarts the host services. An update started by one of them, or by an agent of
the production daemon, would stop with that restart. The **Update job** runs the update apart
from the host services, as a one-shot unit of the OS supervisor:

| | launchd | systemd |
|---|---|---|
| Unit | `nz.tangerinelabs.maelstrom.update`, from `~/.maelstrom/install/update.plist` | transient `maelstrom-update.service` |
| Start | refused while the job has a pid; else `bootout`, then `bootstrap` of the plist | `systemd-run --user --collect`, which refuses the name while the unit runs |
| Runs | once, at load: `RunAtLoad` and no `KeepAlive` | once; `--collect` unloads the unit when it ends, failed or not |
| Early output | `StandardOutPath` is the log | `StandardOutput=append:` the log |

- `launchctl kickstart -k` kills the job's process group, and `systemctl restart` kills the
  unit's cgroup. A child with `start_new_session=True` escapes the group, not the cgroup. Only a
  unit of its own survives both.
- The plist is outside `~/Library/LaunchAgents`. launchd loads that directory at each login,
  and `RunAtLoad` would run the update again.
- The job runs `<resolved current>/venv/bin/mael self update --as-job`. The installer moves
  `current` during the run. The old release stays on disk, so the job's own files do not change.
- The job holds an flock on `~/.maelstrom/install/update.lock` for its whole run, and so does a
  foreground `mael self update`. An update that finds the lock held stops and installs nothing.
  A `bootout` would kill a running launchd job, so `Launchd.submit` refuses while the job has a
  pid.

`mael self update --detach` submits the job and returns. The Welcome dialog's **Update
maelstrom** button calls `POST /api/self/update`, which runs that command from the server's own
venv. The import fence keeps `mael_cli` out of the server, so the server runs the command and
does not import it. The server refuses on a development build: a checkout, or a release run
without `MAEL_PRODUCTION=1`. That job would update the production install, not the server.

### The update record

The record is a file, not an **Operation** (see CONTEXT.md, "Update job"). The job writes `~/.maelstrom/install/update.json` through a temp file and a rename
(`lib/domain/src/mael_domain/release_bundle/update_job.py`):

| Field | Value |
|---|---|
| `state` | `running`, `succeeded` or `failed` |
| `pid` | the job's process |
| `startedAt`, `finishedAt` | ISO times |
| `fromTag`, `toTag` | the `current` release before and after |
| `exitCode` | the installer's exit code |
| `log` | `~/.maelstrom/logs/update.log`, emptied by each job once it holds the lock |

A reader sees a `running` record whose lock is free as `interrupted`: a reboot or a kill stopped
the job. The lock decides, not the pid, because a pid can be reused after a reboot. The installer
moves `current` last, so an interrupted job leaves the old release in place. `--detach` refuses
while the lock is held. `mael self status` and `GET /api/system`
report the record.

## Moving to another host

The production state is `~/.maelstrom/state/state.db` and `~/.maelstrom/state/attachments/`.
`mael self export` and `mael self import` move both as one **State archive**
(`cli/src/mael_cli/self/state_archive.py`). The steps are in `docs/reference/cli.md`.

The import checks the whole zip first, and the extracted database too, so a bad archive changes
nothing. It checks the root again after the stop, because a unit can write before it stops. It extracts to
`state/.import-<pid>/`, so each swap is a rename on one filesystem. It stops every unit,
the scheduler too, so nothing writes during the swap. It migrates after the swap, on the real
path, because a rung receives the database's directory as its root. A `finally` applies the
units again, so a failed migration does not leave the host without its orchestrator.

## The `self` fence

All `mael self` code is in `cli/src/mael_cli/self/`. Two import-linter contracts in
`pyproject.toml` fence it:

- `mael_cli.self` imports no other `mael_cli` module. It uses `mael_domain`, `mael_agent` and
  `mael_common`.
- No `mael_cli` module imports `mael_cli.self`, except `mael_cli.cli`, which registers the group.

A later change can then carve `self` out into a binary of its own.

## Testing

| Suite | Seam |
|---|---|
| `cli/tests/test_self_cli.py` | One smoke test per `mael self` command, with a fake supervisor and a tmp home; `Setup` with fakes |
| `cli/tests/test_update_job.py` | The update job's unit, `Launchd.submit` and `Systemd.submit` with a recorded `run`, and `run_as_job` with a fake installer |
| `cli/tests/test_state_archive.py` | Export and `Import` on real migrated SQLite files under `tmp_path`, with a fake supervisor |
| `cli/tests/test_host_check.py` | `HostCheck` on a fake host, on both platforms |
| `tests/test_install_main.py` | `install-main.sh` against a local repo with a stub `install.sh`; the layout passes `run_project_check`. `install.sh`'s projects-directory step, and the `check-host` order with `--source` |
| `tests/test_release_bundle.py` (e2e) | A real bundle, installed with real uv and `--no-setup`, then served |

The e2e install skips setup, because setup loads units into the real launchd or systemd whatever
home it is given.

## Known limits

- On macOS the units are in the GUI domain, so they start at login, not at boot. Setup over `ssh`
  with no console login cannot load them.
- The orchestrator host service takes port 2772, which is also `_main`'s `ORCHESTRATOR_PORT`. In
  `_main`, start only the services you need, such as `mael env start web -w maelstrom._main`.
  Each setup stops `_main`'s env-managed orchestrator and daemon.
- A host-service orchestrator does not renew the dev certificate. Only `mael env start` does.
- The **Update job** has a Linux test of its `systemd-run` argv only. Its survival of a host
  service restart was checked on macOS alone.
