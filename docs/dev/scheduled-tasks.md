# Scheduled (template) tasks

A *template* task is parked in `template/` status carrying a `schedule` cron
expression, a `trigger`, or both. Hourly, the scheduler runs `mael task
add-scheduled --all-projects --run`. `schedule.fire_due` makes one run per
template that is due, and the command launches each run. A cron template is due when its most recent
fire boundary is newer than its `last_run` watermark. A trigger-only template is
due when a newer run of its workflow has a listed conclusion.

A template with a `trigger` adds one `gh api` read per tick, from
`schedule.due_firings` through `build_runs_github.GhBuildRuns`. A project with no
triggered template makes no `gh` call. For a failed read, see
[the rules](../guide/scheduled-work.md#rules).

Each run starts on the agent daemon. The orchestrator's next `list` adopts the
agent, so the UI shows it. Unlike `task run`, `add-scheduled` ignores
`MAEL_HARNESS_TYPE`, so a hand run fires the same way as the scheduler. It
refuses `--cli` with `--run`; see [the CLI reference](../reference/cli.md#sessions).

## The scheduler is always on

`mael self setup` installs the scheduler on every host, beside the host services.
See [host-services.md](host-services.md). The installer runs setup, so every
install and update keeps it in place.

The scheduler logs to `~/.maelstrom/schedule.log` on macOS, and to the journal on Linux:
`journalctl --user -u maelstrom-schedule.service`. [host-services.md](host-services.md#the-units)
names its units.

The unit runs the `mael` shim, so a run has the production notebook root and
daemon root.

```bash
mael self status                 # diagnose: each unit, loaded or not, and its log
mael self uninstall              # remove the scheduler with the host services
```

`mael self status` is the read-only diagnostic — reach for it first when a
scheduled task didn't fire. It reports whether the supervisor has each unit
loaded, its pid and last exit, and where its log is.

Every `add-scheduled` run writes a dated header line
(`[2026-07-01T09:00:00+00:00] add-scheduled`) to that log before anything else,
so the log records *when* the scheduler last fired even when nothing was due.

## Firing behaviour

- **While awake:** fires hourly at `:00`. On macOS that is
  `StartCalendarInterval`, plus once on load (`RunAtLoad`). On Linux it is the
  timer's `OnCalendar=hourly`.
- **While asleep:** does not fire, and does not wake the machine. The job runs on the
  next wake instead, as a single coalesced catch-up. `fire_due` then makes
  exactly one run per due template — **no backfill** for missed boundaries.

The same `StartCalendarInterval` covers both rows. `man 5 launchd.plist` states
the guarantee:

> Unlike cron which skips job invocations when the computer is asleep, launchd
> will start the job the next time the computer wakes up. If multiple intervals
> transpire before the computer is woken, those events will be coalesced into one
> event upon wake from sleep.

On Linux, the timer's `Persistent=true` gives the same guarantee: a boundary
missed while the machine was off fires once at boot, and one missed in suspend fires on resume.

Nothing pre-empts the wake. An earlier design added a daily `sudo pmset repeat`
wake, which needed a sudo prompt at install and bought no extra firing. Do not
add it back.

`clear_leftover_wake()` cleans up after that design: `mael self uninstall`
cancels a repeating wake if one is set. The read (`pmset -g sched`) is free, so
only a machine that has such a wake sees the `sudo` prompt. It runs from that
command **only** — `mael self setup` runs unattended, from the installer, and
must never block on a password.

Each firing duplicates the template into a run keyed `<template>.<date>`. The run
is created **parentless but dot-named**: its id names it under the template, while
its empty `parent` roots its own chain. The run's follow-ups therefore nest under
the run rather than piling onto the template's chain. See
[`tasks.md`](tasks.md#scheduled-runs) for the `parent`-vs-dot-id distinction.

### The schedule zone

`schedule.py` reads every cron in one zone, the `tz` argument of `previous_fire`, `next_fire`,
`due_firings` and `fire_due`. The callers resolve it with `GlobalConfig.schedule_zone()`, from
`schedule_timezone:`, through `zone.py`. `schedule.py` does not read the config, so its tests
freeze `now` and pass any zone. They pass each `now` twice, in UTC and in `tz`, because the two
take different comparison paths.

- **Wall-clock scan.** The scan steps through naive wall-clock minutes in `tz`. Each match
  resolves to one instant with `fold=0` (PEP 495). `previous_fire` skips an instant after `now`,
  and `next_fire` skips one at or before `now`.
- **Scan start.** Each scan starts one hour (`_DST_SHIFT`) past `now`'s wall minute. So it
  reaches a minute on the other side of a DST shift, such as a first-pass boundary seen from the
  second pass of a repeated hour.
- **Instant comparison.** The guard compares with `now` in UTC. Two datetimes with one `tzinfo`
  compare by wall clock and ignore `fold`.
- **Skipped hour.** A gap minute resolves one hour later, and the guard stops it from firing
  before that time. Cronie fires a gap boundary at the jump (03:00) instead. The `fold=0` rule
  keeps the code simple, so the difference is accepted.
- **Repeated hour.** A second-pass minute resolves to its first-pass instant. That instant is
  not after the watermark, so the second pass does not fire.
- **Watermarks compare as instants.** `_parse_iso` keeps an aware stamp in its own zone. A naive
  stamp reads as host local.
- **Run date.** `date_of` takes a datetime in `tz`, so the `<date>` in a run id is the day in the
  Schedule zone. A firing from a build run keys by `completed_at.astimezone(tz)`.
- **Host zone.** With no key, `zone.host_zone()` gives the host's zone as a DST-aware
  `ZoneInfo`, or UTC. Its docstring lists the sources it tries.

A change of zone does not double fire or skip a run. Only the nearest boundary at or before `now`
counts, and it fires only when it is after the watermark. For example, a host on UTC fired
`0 8 * * 1` at Mon 08:00 UTC, which is Mon 21:00 NZDT. On Tue in NZ, the nearest boundary is
Mon 08:00 NZDT, before that watermark, so nothing fires until the next Mon. When a run id for the
day already exists, `fire_due` makes nothing and moves only the watermark.
