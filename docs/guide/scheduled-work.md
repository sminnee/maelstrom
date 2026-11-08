# Scheduled work

Run a task on a schedule or when a build finishes — a nightly dependency check, a weekly triage
sweep, a fix for a failed nightly build.

## Templates

A **template** is a task parked in `template/` status carrying a `schedule` cron
expression, a `trigger`, or both. It is a reusable recipe, never actionable itself.

```bash
mael task add "Triage Sentry issues" \
  --template \
  --schedule '0 9 * * 1-5' \
  --mode auto \
  --content-file triage-brief.md
```

`--schedule` takes a standard five-field cron expression. It is acted on **only** for
template tasks; on an ordinary task it is inert.

Make an existing task a template:

```bash
mael task status template <id>
mael task update <id> --schedule '0 9 * * 1-5'
mael task update <id> --schedule ''          # clear it
mael task update <id> --trigger gh-action/nightly.yml
```

List them:

```bash
mael task list --status template
```

## Firing

Each firing duplicates the template into a **run** named `<template>.<date>`, for example
`triage.2026-07-02`, and advances the template's `last_run` watermark.

The run is **dot-named but parentless**. Its id names it under the template; its empty
`parent` roots its own chain. So each firing's follow-ups nest under **that run**, not the
template — every firing is isolated, with its own branch and pull request, instead of piling
onto the template's chain.

That is the [`parent` vs dot-id separation](tasks.md#dotted-ids-express-lineage) doing real
work.

Fire due templates by hand:

```bash
mael task add-scheduled --run                  # this project
mael task add-scheduled --all-projects --run   # every project
```

Without `--run`, runs are created but not launched.

A launched run is a driven agent. The orchestrator UI shows it on the run's node card and on the
desk. Attach there, or with `mael agent attach <id>`. `--run` launches every due run, so it
refuses `--cli`.

The run starts on the production agent daemon, the `agent-daemon` host service. If that daemon
is down, the run goes back to `todo`. The next hourly fire does not retry it. Launch it with
`mael task run <id>`.

## Fire on a finished build

A `trigger` fires a template when a GitHub Actions run finishes. Use it to start an agent
when the nightly build fails:

```bash
mael task add "Fix the nightly build" --template --trigger gh-action/nightly.yml --mode auto
```

The trigger is `gh-action/<workflow>[@<branch>] [<conclusion>,...]`.

| Part | Meaning | Default |
|---|---|---|
| `<workflow>` | The workflow file name, such as `nightly.yml`. Not its display name. | Required |
| `@<branch>` | The branch the run is on. | `main` |
| `<conclusion>,...` | GitHub run conclusions that fire, such as `failure` or `success`. `failed` means `failure,timed_out,startup_failure`. | `failed` |

To fire on a green release build instead:

```bash
mael task add "Announce the release" --template \
  --trigger 'gh-action/release.yml@release success'
```

A run whose conclusion is not on the list does not fire. With the default list, a
`cancelled` run does not fire.

### Trigger alone

On each hourly tick, maelstrom reads the workflow's completed runs. It takes the newest run
that finished after the watermark. That run fires when its conclusion is on the list, and the
watermark moves to it either way. A failure that a newer green run has already fixed does not
fire. The delay is one hour or less.

The run is named by the date the build finished, such as `nightly.2026-10-07`.

### Trigger and schedule

With a `schedule` as well, the cron boundary decides when maelstrom looks. The boundary fires
only when the newest completed run has a listed conclusion. That run need not be new: a build
that stays red fires at every boundary. Otherwise the boundary is skipped, and the watermark
still moves to it.

```bash
mael task add "Morning build triage" --template \
  --schedule '0 9 * * 1-5' --trigger gh-action/nightly.yml
```

### Rules

- **There is no backfill.** Runs that finished before the template was made do not fire.
- **One run per day.** A second listed run on the same day fires nothing, because its run id is
  taken. The watermark still moves past it.
- **A failed read is unknown.** When `gh` cannot read the runs, nothing fires and the
  watermark stays. The next tick reads again. The scheduler's log records a warning.
- **The run's content gains a "Build run" section.** It names the workflow, the branch, the
  conclusion, the run URL and the commit, and gives the `mael gh check-log` command for the
  failed jobs.

## The scheduler is always on

`mael self setup` installs the scheduler on every host, next to the host services. The
installer runs setup, so an install or an update needs no extra step.

```bash
mael self status                 # diagnose: the units, their state and their logs
mael self uninstall              # remove the scheduler with the host services
```

## When it fires

The scheduler runs `mael task add-scheduled --all-projects --run` hourly.

| State | Behaviour |
|---|---|
| Awake | Fires hourly at `:00`. On macOS it also fires once when the agent loads. |
| Asleep | Does not fire, and does not wake the machine. The job runs on the next wake instead, as **one** coalesced catch-up. |

Maelstrom does not wake a sleeping machine, and does not need to. launchd starts a missed job on
the next wake and coalesces every missed interval into one event. The systemd timer has
`OnCalendar=hourly` and `Persistent=true`, which does the same.

**There is no backfill.** After a missed period you get exactly one run per due template,
not one per missed boundary. A machine asleep for a week does not wake to seven runs of the
same template.

Older versions on macOS could set a daily `pmset` wake. `mael self uninstall` clears a wake
left over from it, and asks for **sudo** when it finds one. Machines that never set one see no
prompt.

To clear a leftover wake and keep the scheduler:

```bash
pmset -g sched            # a line under "Repeating power events" is the leftover
sudo pmset repeat cancel
```

macOS keeps **one** system-wide repeating wake, so `pmset repeat cancel` clears a wake you
set yourself as well.

### The schedule zone

The cron in a `schedule` is read in the **Schedule zone**. Set it in `~/.maelstrom/config.yaml`:

```yaml
schedule_timezone: Pacific/Auckland
```

With no key, the host's own zone applies, and the listing and the UI show it as
`<zone> (host local)`. One zone covers every template on the host. A host on
UTC that serves a person in NZ needs the key: without it, `0 8 * * 1` fires at 08:00 UTC.

Daylight saving time (DST) changes move the clock. A boundary in an hour that DST changes fires
once:

| Change | Example (NZ) | Behaviour |
|---|---|---|
| Skipped hour | 02:00 → 03:00 in September | A boundary in the gap fires one hour later by the clock. `30 2 * * *` fires at 03:30. |
| Repeated hour | 03:00 → 02:00 in April | A boundary in the repeated hour fires at its first occurrence only. `*/5 * * * *` does not fire again in the second pass. |

Four places show the zone:

- `mael task list --status template` gives `NEXT-FIRE` with its UTC offset, and ends with
  `Schedules read in <zone>.`
- `mael task show` prints `schedule: <cron> (<zone>)` for a template.
- Each `add-scheduled` line in `schedule.log` starts with the time and the zone.
- The task editor in the orchestrator UI shows `Schedule: <cron> — <zone>` for a template.

An unknown zone name stops `add-scheduled` with an error, and nothing fires. The listing and the
UI show `unknown zone <name>`. The orchestrator reads the key at start, so run
`mael self restart orchestrator` after a change. Until then, `mael task show` and the UI name the
old zone.

## When a scheduled task did not fire

Run `mael self status` first. See
[troubleshooting.md](troubleshooting.md#a-scheduled-task-did-not-fire) for what it reports
and the common causes.

## See also

- [Scheduled tasks](../dev/scheduled-tasks.md) — the launchd and systemd mechanics in full.
- [Tasks](tasks.md) — templates, ids and chains.
