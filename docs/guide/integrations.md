# Integrations

Linear, Sentry, GitHub, Slack and UptimeRobot streamline the workflow. They are not peers of
the components in [concepts.md](concepts.md) — they remove manual steps.

All of them are optional. Maelstrom works without any of them.

## Credentials

Each API key resolves in this order:

1. The environment variable.
2. A `.env` file, searched upward from the current directory.
3. `~/.maelstrom/config.yaml`.

```yaml
# ~/.maelstrom/config.yaml
linear:
  api_key: "lin_api_xxx"
sentry:
  api_key: "sntrys_xxx"
uptimerobot:
  api_key: "u796748-xxx"
slack:
  webhooks:
    alerts: "https://hooks.slack.com/services/T000/B000/xxx"
```

This file holds plaintext secrets. `mael doctor` checks its permissions.

---

## Linear

Linear is the **product-level mirror**. Read briefs there and mirror status there. The plan
of record lives in the notebook chain, not in a Linear description.

```yaml
# .maelstrom.yaml
linear:
  team_id: "your-team-uuid"
  workspace_labels: [alpha, bravo, charlie]
  product_label: "YourProduct"
```

### Reading and planning

```bash
mael linear list-tasks                    # the current cycle
mael linear list-tasks --status "In Progress"
mael linear read-task PROJ-123            # details, subtasks, comments
mael linear plan PROJ-123                 # seed a planning task and launch it
```

`mael linear plan` is the usual entry point. See [planning.md](planning.md).

### Status transitions

```
Todo ──────────────► Planned        set-status … planned, or create-subtask
Planned/Todo ──────► In Progress    start-task, or set-status … in-progress
In Progress ───────► In Review      mael gh create-pr PROJ-123
In Review ─────────► Unreleased     set-status … done
Unreleased ────────► Done           mael linear release
```

```bash
mael linear start-task PROJ-123                  # In Progress + workspace label
mael linear set-status PROJ-123 in-progress
mael linear set-status PROJ-123 done             # → "Unreleased"
mael linear release                              # promote Unreleased → Done
```

`set-status` applies to the named issue **as-is**. It does not transition parents or
subtasks. Move a parent yourself once its subtasks are complete.

`done` maps to "Unreleased" rather than "Done" on purpose: work that has merged is not yet
work that has shipped. `mael linear release` promotes everything carrying the product label
when you actually ship.

### Automating status from tasks

Lifecycle actions on a task fire a Linear transition, so the chain mirrors itself:

```yaml
pre-action: linear.in-progress     # fires when the task launches
```

Do **not** put `post-action: linear.done` on execute steps. The finishing sequence closes
the task at PR push, before the CI watch, so it would flip Linear to Unreleased while CI is
still running and overwrite the "In Review" that `create-pr` just set.

### Other commands

```bash
mael linear create-task "Title" "Description"
mael linear create-subtask PROJ-123 "Subtask title"
mael linear add-comment PROJ-123 notes.md
mael linear write-plan PROJ-123 plan.md    # store a plan in the description
mael linear read-plan PROJ-123
mael linear edit-plan PROJ-123 old.md new.md
mael linear edit-plan PROJ-123 -s "old" "new"
```

---

## GitHub

Pull requests and CI. Needs the `gh` CLI:

```bash
brew install gh && gh auth login
```

```bash
mael gh create-pr PROJ-123 --squash        # create or push
mael gh read-pr                            # status, comments, CI
mael gh read-pr --wait                     # block until CI finishes
mael gh show-code --uncommitted            # review before committing
mael gh check-log <run_id> --failed-only   # failing steps
mael gh download-artifact <run_id> <name>  # traces, screenshots
```

See [pull-requests.md](pull-requests.md) for the whole flow.

---

## Sentry

Turn production errors into work.

```yaml
# .maelstrom.yaml
sentry:
  org: "your-org"
  project_id: "your-project-slug"
```

> These keys nest under `sentry:`. Flat `sentry_org:` and `sentry_project:` keys are **not
> read**, and the integration stays silently unconfigured.

```bash
mael sentry list-issues                    # unresolved, prod
mael sentry list-issues --env staging
mael sentry list-issues --since 24h
mael sentry get-issue <issue-id>           # stacktrace and variables
mael sentry resolve-issue <issue-id>       # resolved in the next release
```

Prioritise by escalating trend, then recency, then frequency.

`resolve-issue` is a **write** action against production monitoring. Use it when an issue is
confirmed fixed in current code — for example the reported release pre-dates the fix commit
and the call sites now handle the case. Confirm with the user first.

---

## Slack

Post notifications from a session or a scheduled run.

```yaml
# ~/.maelstrom/config.yaml
slack:
  webhooks:
    alerts: "https://hooks.slack.com/services/T000/B000/xxx"
    releases: "https://hooks.slack.com/services/T000/B001/yyy"
```

```bash
mael slack post "Deploy finished"
mael slack post "Release notes" --channel releases
echo "from a pipeline" | mael slack post
```

With no `--channel`, the **first** webhook in the map is used. The order you write them in
is preserved.

---

## UptimeRobot

Check whether anything is down.

```yaml
# .maelstrom.yaml
uptimerobot:
  monitors: ["796748268", "796748269"]
```

```bash
mael uptimerobot monitors           # discover ids — run this once, for setup
mael uptimerobot status             # current status, 24h/7d/30d uptime
mael uptimerobot outages            # recent down events, newest first
mael uptimerobot outages --since 7d --limit 50
```

Use `status` for "is anything down right now?" and `outages` to investigate an incident.
`--since` accepts `30m`, `24h`, `7d`.

With no monitors configured, the commands fall back to every monitor on the account. Run
`monitors` once to find the ids worth listing.

## See also

- [Configuration reference](../reference/configuration.md) — every key.
- [Environment variables](../reference/environment.md) — key resolution order.
