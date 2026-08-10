# Scheduled (template) tasks

A *template* task is parked in `template/` status carrying a `schedule` cron
expression. Hourly, a launchd LaunchAgent runs `mael task add-scheduled
--all-projects --run`, which consults `schedule.due_templates` and launches any
template whose most recent fire boundary is newer than its `last_run`
watermark.

## The launchd agent is opt-in

The agent is **not** installed automatically — `ensure_schedule_agent()` (wired
into `mael install` / `mael self-update`) is gated on an opt-in marker
(`~/.maelstrom/schedule.enabled`). Without the marker it is a deliberate no-op,
so a background scheduler is never imposed on every checkout or CI box.

```bash
mael schedule install            # opt in: write marker + load the agent
mael schedule uninstall          # opt out: remove marker, unload, clear old wake
mael schedule status             # diagnose: marker / plist / loaded / log tail
```

The marker is a bare presence flag — an empty file. `install` needs no sudo and
asks nothing, so it runs unattended.

`mael schedule status` is the read-only diagnostic — reach for it first when a
scheduled task didn't fire. It reports whether the marker and plist exist,
whether launchd has the job loaded, and the tail of `~/.maelstrom/schedule.log`.

Every `add-scheduled` run writes a dated header line
(`[2026-07-01T09:00:00+00:00] add-scheduled`) to that log before anything else,
so the log records *when* the agent last fired even when nothing was due.

## Firing behaviour

- **While awake:** fires hourly at `:00` (`StartCalendarInterval`) plus once on
  load (`RunAtLoad`).
- **While asleep:** does not fire, and does not wake the Mac. The job runs on the
  next wake instead, as a single coalesced catch-up. `due_templates` then yields
  exactly one run per due template — **no backfill** for missed boundaries.

The same `StartCalendarInterval` covers both rows. `man 5 launchd.plist` states
the guarantee:

> Unlike cron which skips job invocations when the computer is asleep, launchd
> will start the job the next time the computer wakes up. If multiple intervals
> transpire before the computer is woken, those events will be coalesced into one
> event upon wake from sleep.

Nothing pre-empts the wake. An earlier design added a daily `sudo pmset repeat`
wake, which needed a sudo prompt at install and bought no extra firing. Do not
add it back.

`clear_leftover_wake()` cleans up after that design: `mael schedule uninstall`
cancels a repeating wake if one is set. The read (`pmset -g sched`) is free, so
only a machine that has such a wake sees the `sudo` prompt. It runs from the CLI
command **only** — `ensure_schedule_agent()` is called by `mael install` and
`mael self-update`, which run unattended and must never block on a password.

Each firing duplicates the template into a run keyed `<template>.<date>`. The run
is created **parentless but dot-named**: its id names it under the template, while
its empty `parent` roots its own chain, so the run's follow-ups nest under the run
rather than piling onto the template's chain. See
[`tasks.md`](tasks.md#scheduled-runs) for the `parent`-vs-dot-id distinction.
