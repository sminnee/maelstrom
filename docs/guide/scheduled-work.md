# Scheduled work

Run a task on a schedule — a nightly dependency check, a weekly triage sweep.

## Templates

A **template** is a task parked in `template/` status carrying a `schedule` cron
expression. It is a reusable recipe, never actionable itself.

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
mael task add-scheduled --run --here           # in the current shell
```

Without `--run`, runs are created but not launched.

## The scheduler is opt-in per machine

Maelstrom does **not** install a background scheduler for you. A launchd agent that quietly
starts agent sessions is not something to impose on every checkout and CI box, so it is
gated behind an explicit marker at `~/.maelstrom/schedule.enabled`.

```bash
mael schedule install            # opt in: write the marker, load the agent
mael schedule uninstall          # opt out: remove the marker, unload, clear the wake
mael schedule status             # diagnose
```

Until you run `install`, the wiring in `mael install` and `mael self-update` is a deliberate
no-op.

## When it fires

The agent runs `mael task add-scheduled --all-projects --run` hourly.

| State | Behaviour |
|---|---|
| Awake | Fires hourly at `:00`, plus once when the agent loads. |
| Asleep, no `--wake-at` | Does not fire and does not wake the Mac. On the next wake, launchd runs **one** coalesced catch-up. |
| Asleep, with `--wake-at` | A `pmset` wake brings the Mac up in time for the next tick. |

**There is no backfill.** After a missed period you get exactly one run per due template,
not one per missed boundary. A machine asleep for a week does not wake to seven runs of the
same template.

## Waking a sleeping Mac

```bash
mael schedule install --wake-at 09:00
```

A user LaunchAgent cannot wake the machine. Only the OS power scheduler can. So this is a
separate step that needs **sudo**, prompted at install.

Caveats:

- `HH:MM` is the machine's **local** time, matching the launchd tick it lines up with. (The
  cron `schedule` math and the log timestamps are in **UTC**.)
- macOS supports **one** system-wide repeating wake. Installing replaces any prior one.
- The wake is set one minute before `HH:MM`.
- A clamshell laptop on battery may ignore it.

## When a scheduled task did not fire

Run this first:

```bash
mael schedule status
```

It reports the marker, the plist, whether launchd has the job loaded, the `pmset` wake line,
and the tail of `~/.maelstrom/schedule.log`.

Every `add-scheduled` run writes a dated header to that log before doing anything else, so
the log records *when* the agent last fired even when nothing was due. No header means the
agent did not run; a header with no runs means nothing was due.

## See also

- [Scheduled tasks](../dev/scheduled-tasks.md) — the launchd mechanics in full.
- [Tasks](tasks.md) — templates, ids and chains.
