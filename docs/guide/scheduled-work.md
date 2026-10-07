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
mael task add-scheduled --run --cli            # in a new cmux workspace
mael task add-scheduled --run --here           # in the current shell
```

Without `--run`, runs are created but not launched.

A launched run is a driven agent with no cmux pane. The orchestrator UI shows it on the run's
node card and on the desk. Attach there, or with `mael agent attach <id>`. Use `--cli` to open
a cmux workspace instead.

The run starts on the everyday agent daemon, which `mael self-env start` runs. If that daemon
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
  watermark stays. The next tick reads again. `schedule.log` records a warning.
- **The run's content gains a "Build run" section.** It names the workflow, the branch, the
  conclusion, the run URL and the commit, and gives the `mael gh check-log` command for the
  failed jobs.

## The scheduler is opt-in per machine

Maelstrom does **not** install a background scheduler for you. A launchd agent that quietly
starts agent sessions is not something to impose on every checkout and CI box, so it is
gated behind an explicit marker at `~/.maelstrom/schedule.enabled`.

```bash
mael schedule install            # opt in: write the marker, load the agent
mael schedule uninstall          # opt out: remove the marker, unload
mael schedule status             # diagnose
```

`install` needs no sudo and asks you nothing. `uninstall` asks for sudo only on a machine
carrying a leftover wake — see below.

Until you run `install`, the wiring in `mael install` and `mael self-update` is a deliberate
no-op.

## When it fires

The agent runs `mael task add-scheduled --all-projects --run` hourly.

| State | Behaviour |
|---|---|
| Awake | Fires hourly at `:00`, plus once when the agent loads. |
| Asleep | Does not fire, and does not wake the Mac. The job runs on the next wake instead, as **one** coalesced catch-up. |

Maelstrom does not wake a sleeping Mac, and does not need to. launchd starts a missed job on
the next wake and coalesces every missed interval into one event.

**There is no backfill.** After a missed period you get exactly one run per due template,
not one per missed boundary. A machine asleep for a week does not wake to seven runs of the
same template.

Older versions offered `mael schedule install --wake-at HH:MM`, which set a daily `pmset`
wake. That option is gone. `mael schedule uninstall` clears a wake left over from it, and
asks for **sudo** when it finds one. Machines that never used `--wake-at` see no prompt.

To clear a leftover wake without opting out of the scheduler:

```bash
pmset -g sched            # a line under "Repeating power events" is the leftover
sudo pmset repeat cancel
```

macOS keeps **one** system-wide repeating wake, so `pmset repeat cancel` clears a wake you
set yourself as well.

## When a scheduled task did not fire

Run `mael schedule status` first. See
[troubleshooting.md](troubleshooting.md#a-scheduled-task-did-not-fire) for what it reports
and the common causes.

## See also

- [Scheduled tasks](../dev/scheduled-tasks.md) — the launchd mechanics in full.
- [Tasks](tasks.md) — templates, ids and chains.
