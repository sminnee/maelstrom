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
mael schedule install --wake-at 09:00   # also wake a sleeping Mac (see below)
mael schedule uninstall          # opt out: remove marker, unload, clear wake
mael schedule status             # diagnose: marker / plist / loaded / wake / log tail
```

`mael schedule status` is the read-only diagnostic — reach for it first when a
scheduled task didn't fire. It reports whether the marker and plist exist,
whether launchd has the job loaded, the `pmset` repeating-wake line, and the
tail of `~/.maelstrom/schedule.log`.

Every `add-scheduled` run writes a dated header line
(`[2026-07-01T09:00:00+00:00] add-scheduled`) to that log before anything else,
so the log records *when* the agent last fired even when nothing was due.

## Firing behaviour

- **While awake:** fires hourly at `:00` (`StartCalendarInterval`) plus once on
  load (`RunAtLoad`).
- **While asleep, no `--wake-at`:** does not fire and does not wake the Mac. On
  the next wake, launchd runs a single coalesced catch-up; `due_templates`
  yields exactly one run per template — **no backfill** for missed boundaries.
- **While asleep, with `--wake-at HH:MM`:** a `pmset` wake brings the Mac up so
  the next launchd tick runs the job.

A user LaunchAgent alone cannot wake the machine — only the OS power scheduler
(`pmset`) can — which is why wake support is a separate, sudo-requiring step.

## `--wake-at` caveats

- `HH:MM` is the machine's **local** time — `pmset` schedules wakes in local
  time, as does the launchd hourly tick the wake lines up with. (Note the
  contrast: the cron `schedule` math and the `schedule.log` header timestamp are
  in **UTC**. The wake only has to bring the Mac up in time for the next local
  hourly tick, so local time is the right unit for it.)
- Needs **sudo** (prompted interactively at install; never invoked from the
  agent itself).
- Schedules **one** daily wake, set one minute before `HH:MM` to avoid a
  wake/tick race (`--wake-at 09:00` → `pmset` wake at `08:59`).
- `pmset repeat` allows only **one** system-wide repeating wake, so installing
  replaces any prior one; `uninstall` (or installing without `--wake-at`) clears
  it.
- Clamshell-on-battery laptops may ignore the wake.
- A wake that fires into nothing is benign — the Mac idles briefly, then
  re-sleeps on its normal timer.
