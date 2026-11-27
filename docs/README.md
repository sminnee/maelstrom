# The map of maelstrom

This file lists maelstrom's subdomains and subsystems, and where the code of each one is. Read it
to find the glossary and the feature files of the part you work in.

A **Subdomain** is a part of what maelstrom does, with one language. Its subsystems share one
glossary. A **Subsystem** owns its code at each
layer: its model in `mael_domain`, its commands in the CLI, its routes in the orchestrator server,
its views in the orchestrator UI, and the skills that drive it. The root `CONTEXT.md` defines
both terms.

The Platform subdomain is infrastructure: each of its subsystems is the frame of one layer, or a
service that every other subsystem uses.

A use case that changes the domain types of several subsystems belongs to the subsystem of its
verb. Launch a task is in Task runs. Close a worktree is in Projects and worktrees. Dismiss is in
the Desk.

Path prefixes in this file:

| Prefix | Path |
| --- | --- |
| `D/` | `lib/domain/src/mael_domain/` |
| `C/` | `cli/src/mael_cli/` |
| `O/` | `orchestrator-api/src/mael_orchestrator/` |
| `U/` | `orchestrator-ui/src/` |
| `S/` | `shared/plugins/maelstrom/skills/` |

A layer file that serves several subsystems is split by section: `O/server.py`, `O/routes.py`,
`O/sources.py`, `O/validate.py`, `D/protocol.py`, `U/api/`, `U/selectors/` and `U/protocol/`.
Each subsystem owns its sections. The file as a whole is in the Platform subdomain.

## Subsystems

### Tasks

- **Task tracker** — holds each task, its status and its order. Code: `D/task.py`, `D/task_table.py`, `D/task_actions.py`, `D/task_metadata_generator.py`, `D/schedule.py`, `D/zone.py`, `C/task_cli.py`, `U/tasklist/`, `U/newwork/`, `S/planning/`, `S/plan-task/`, `S/plan-next-step/`. Specs: `docs/specs/tasks/task-tracker/`
- **Task runs** — launches a task on an agent, and reads the task against that agent. Code: `D/task_launch.py`, `D/session_discovery.py`, `D/session_logs.py`. Specs: none yet
- **Task documents** — keeps what an agent shows the user, and the user's verdict. Code: `D/task_documents.py`, `D/task_attachments.py`, `D/document_tags.py`, `D/link_cards.py`, `D/attachments.py`, `D/file_registry.py`, `D/sarif.py`, `C/doc_cli.py`, `U/documents/`, `U/decisions/`, `U/markdown/LinkCard.tsx`. Specs: none yet
- **Comms** — tells people outside the team when work lands. Code: `D/comms.py`, `D/comm_store.py`, `D/comm_codename.py`, `C/comms_cli.py`, `U/comms/`. Specs: none yet

### Code

- **Projects and worktrees** — gives each piece of work an isolated checkout. Code: `D/worktree.py`, `D/worktree_model.py`, `D/worktree_ops.py`, `D/worktree_steps.py`, `D/worktree_close.py`, `D/worktree_trash.py`, `D/worktree_config.py`, `D/ports.py`, `D/mv_project.py`, `D/git_status.py`, `D/project_scaffold.py`, `D/rebase_repair.py`, `D/list_all.py`, `D/cmux/`, `C/project_cli.py`, `C/mv_project_cli.py`, `C/git_cli.py`, `C/project_check.py`, `C/worktree_launcher.py`, `U/worktrees/`, `U/projects/`, `S/reopen-branch/`, `S/resolve-rebase-conflicts/`, `S/cmux/`, `S/setup-project/`. Specs: none yet
- **Dev environments** — runs the services of a worktree. Code: `D/env.py`, `D/env_store.py`, `D/services.py`, `D/dev_cert.py`, `C/env_cli.py`, `U/devenv/`. Specs: none yet
- **Changes review** — lets the user read and answer a worktree's diff. Code: `D/worktree_changes.py`, `D/diff_groups.py`, `D/language_servers.py`, `O/language_servers.py`, `O/lsp_client.py`, `U/changes/`, `U/rendered/`. Specs: none yet
- **Live feedback** — carries visual feedback between a dev-server page and the agents. Code: `vite-plugin-mael-jig/`, `D/feedback.py`, `D/live_feedback_key.py`, `O/live_feedback.py`. Specs: none yet

### Delivery

- **Pull requests and landing** — takes a branch to `main` and follows it to live. Code: `D/github.py`, `D/github_model.py`, `D/pr_ledger_store.py`, `D/landing.py`, `D/landing_github.py`, `D/landing_store.py`, `D/merge_request.py`, `D/merge_request_store.py`, `D/build_runs.py`, `D/build_runs_github.py`, `O/build_queue.py`, `C/github_cli.py`, `C/merge_cli.py`, `S/watch-pr/`. Specs: none yet
- **Quality checks** — gates work before it merges. Code: `O/gate_source.py`, `O/gate_table.py`, `O/machine_metrics.py`, `pipeline.toml`. Specs: none yet

### Agents

- **Agents** — runs agents, answers their waits, and counts their spend. Code: `lib/agent/`, `agent-daemon/`, `D/agent_store.py`, `D/agent_cost.py`, `D/normalise.py`, `D/external_event.py`, `D/orchestrator_data.py`, `O/daemon_bridge.py`, `O/codex_bridge.py`, `O/codex_daemon.py`, `O/agent_queue.py`, `O/transcript_log.py`, `O/loop_watch.py`, `C/agent_cli.py`, `C/agent_tui.py`, `C/agent_view.py`, `C/session_cli.py`, `U/session/`, `S/mael/`, `shared/agent-prompt.md`, `shared/claude-header.md`. Specs: none yet
- **Remote hosts** — delegates a launch to a second orchestrator server. Code: `O/remote_hosts.py`, `O/clients.py`, `D/ssh_target.py`. Specs: none yet

### Desk

- **Desk** — shows the user the work in progress and what waits on them. Code: `D/desk_store.py`, `O/desk.py`, `U/canvas/`, `U/deck/`, `U/gesture/`, `shared/investigation-prompt.md`. Specs: none yet

### Method

- **minnstack** — a way of working that a project can use without maelstrom. Code: `shared/plugins/minnstack/`. Specs: none yet
- **Wiki** — holds the design patterns that apply to more than one project. Code: `D/wiki.py`, `C/wiki_cli.py`. Specs: none yet

### Platform

- **State database** — stores every canonical and cached table. Code: `D/state_db/`, `D/notebook_root.py`. Specs: none yet
- **Orchestrator server** — builds the world and serves it. Code: `O/server.py`, `O/routes.py`, `O/world.py`, `O/world_build.py`, `O/sources.py`, `O/validate.py`, `O/notices.py`, `O/hubs.py`, `O/cli.py`, `D/protocol.py`. Specs: none yet
- **Operations and console** — records each slow change to the world, step by step. Code: `D/operations.py`, `D/operation_store.py`, `O/operations.py`, `U/console/`. Specs: none yet
- **CLI** — the frame of the `mael` command, and the shell and table helpers. Code: `lib/common/`, `C/cli.py`, `C/orchestrator_client.py`, `C/json_flag.py`, `C/table_cli.py`, `C/templates/`, `D/config.py`, `D/context.py`. Specs: none yet
- **UI shell** — the frame of the orchestrator UI: slots, the panel, layouts and tokens. Code: `U/shell/`, `U/layout/`, `U/panel/`, `U/nav/`, `U/ui/`, `U/store/`, `U/styles/`, `U/markdown/`, `U/live/`, `U/fake/`, `U/test/`, `U/api/`, `U/selectors/`, `U/protocol/`, `U/App.tsx`, `U/main.tsx`, `U/eventsUrl.ts`. Specs: none yet
- **Integrations** — the clients of the outside services: Linear, Sentry, Slack, UptimeRobot and OpenAI. Code: `D/integrations/`, `C/integrations/`, `O/linear_source.py`. Specs: none yet
- **Install** — puts maelstrom on a host and keeps it up to date. Code: `D/release_bundle/`, `D/install_status.py`, `D/system_info.py`, `D/shared_dir.py`, `C/self/`, `bin/`, `U/welcome/`. Specs: none yet

Gherkin runner: none

## Where the documents are

| Document | Path |
| --- | --- |
| The terms that two or more subdomains use | `CONTEXT.md` |
| The terms that one subdomain defines, under one heading for each subsystem | `docs/specs/<subdomain>/CONTEXT.md` |
| The contract of a command or a query | `docs/specs/<subdomain>/<subsystem>/<name>.feature` |
| A decision and its rollout | `docs/adr/` |
| How the code works | `docs/dev/` |
| How to use maelstrom | `docs/guide/`, `docs/reference/` |

The Tasks subdomain has a glossary. The terms of each other subdomain are in the root
`CONTEXT.md` until that subdomain gets its own.
