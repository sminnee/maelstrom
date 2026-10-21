# The CLI over the orchestrator API

Most `mael` commands should call the orchestrator server, not open `state.db` themselves. Then the
server is the one writer of the state it holds, and `mael_domain` belongs to the server alone. This
note sorts every command into a class, names the API gaps, and gives the migration order.

`mael task show`, `read`, `list` and `status` are the pilot. They call the server today, as do
`mael list`, `mael list-all`, and the creating commands `mael task add`, `promote` and
`load-many`.

## Why two writers fail

The CLI and the server call the same `mael_domain` functions on the same database. The server
also holds state in memory, so a direct CLI write goes stale in it:

- The server reads the desk, the agent records and the task attachments once, at start. It does
  not see a CLI write to them. Its next desk save overwrites that write.
- The server polls tasks and comms every 2 s, so a CLI write shows late.
- The server polls worktrees every 60 s. `mael gh create-pr`, `mael git merge` and
  `mael close --trash` post `/api/worktrees/refresh` with the changed branch to make up for it.

When the server makes the write, it publishes the change at once, and nothing goes stale.

Until every task write moves, `task run`, `task next --run`, `task log`, `task edit` and
`task add-scheduled` still write the database. So a task read, a status move and each create route
refresh the server's tasks first. That costs one revision query when nothing moved, and `mael task run`
then `mael task status done` finds the moved task.

## The Orchestrator client

`cli/src/mael_cli/orchestrator_client.py` is the **Orchestrator client**. The agent daemon has the
same shape: a wire contract and a client surface, so the CLI never imports `mael_daemon`.

| Question | Answer |
|---|---|
| Where is the server? | The `orchestrator_url:` key in `$MAEL_NOTEBOOK_ROOT/config.yaml`: the global file for everyday use, and a dev environment's own file beside its `state.db`. The notebook and its server are found the same way, so a dev environment never reaches the everyday server. There is no default, because a port is never hard-coded. |
| What does it send? | Ids. The server runs on this machine, so the client resolves the cwd, `$MAEL_TASK_ID` and `--project` to the wire id `<project>/<notebook id>` itself. |
| What if the key is unset? | `OrchestratorUnconfigured`. The message names the key. |
| What if no server answers? | `OrchestratorUnreachable`. The message names the URL and `mael self-env start orchestrator`. |
| What if the server refuses? | `OrchestratorRefused`, with the `{"error": {code, message}}` the server sent. A command maps `unknown_id` to its own "not found" message. |

There is no fallback to the database. For a write, a fallback brings back the second writer. For
a read, it answers from a different copy than the next write goes to.

`tell_orchestrator` is the one best-effort call. `mael gh create-pr` uses it after the push, so it
raises nothing.

The wire types are the TypedDicts in `mael_domain/protocol.py`. They move to a wire package with
no domain in the package split below.

## Command classes

| Class | Commands | Why |
|---|---|---|
| **A. Thin over REST: the routes exist** | `task show/read/list/status/rm/update`, `list`, `list-all`, `comms new/list/close/edit`, `close`, `rm`, `close --trash`, `agent cost`, `task run` (as a launch), `task add/promote/load-many` | The state is the server's. Both sides already call the same domain function. |
| **B. Thin after API work** | `task next/log/reconcile`, `comms link/landings`, `sync`, `env start/stop/restart/reset/status`, `gh create-pr`, `gh link-pr` | Each needs a new route or field. `sync`, `create-pr` and `env start` also need a streamed long command. |
| **C. Stays local** | `install`, `self-update`, `self-env *`, `doctor`, `cmux status`, `schedule *`, `session end/info/list`, `task edit`, `comms edit` with no options, `task add --run --here`, `task add --edit`, `task load-many --run --here`, `agent attach/tail` | Bootstrap must work with the server down: `self-env start orchestrator` starts it. The rest use the TTY or `$EDITOR`, exec into the caller's shell, or signal the caller's parent process. |
| **D. Not state: no reason to move** | `gh read-pr/check-log/download-artifact/show-code/has-pr/wait-for-pr`, `git status/squash-branch/uncommit-branch/merge`, `base/stack-tip/promote/eject`, `wiki *`, `linear *` but `plan`, `sentry *`, `uptimerobot *`, `slack *` | They call git or a third-party API. The server holds none of that state, so a hop gains nothing. |
| **E. Already a thin client** | `agent show/say/run/approve/…` | They speak the agent daemon's wire contract. |

## Done

`mael list` and `mael list-all` read `GET /api/worktrees?max_age=10`. See the route table in
[orchestrator-server.md](orchestrator-server.md). The PR state they show comes from the server's
`PrStateCache` alone.

`mael task add`, `promote` and `load-many` post to `POST /api/tasks`, `POST /api/tasks/promote`
and `POST /api/tasks/load-many`. `mael linear plan` calls `task add`, so it moved with it. The
client resolves the environment and the files:

- `$MAEL_TASK_PARENT` becomes `parent`, and `$MAEL_TASK_ID` becomes the promote's `after`.
- A bare id from a flag becomes a wire id. `--follow-end '*'` stays `*`.
- `promote` reads the draft files and sends their text. It deletes the files after a `2xx`
  reply, and leaves them on a refusal.
- `--run` sets `launch`, and the server starts the agent on the daemon. `--run --here` and
  `--edit` stay local: they exec into the caller's shell or open `$EDITOR`.

## API gaps

| Gap | Commands it blocks |
|---|---|
| No route for a task log append | `task log` |
| No actionable or next filter on `GET /api/tasks` | `task next`. It could filter the full list, as `task list` does. |
| No streamed long command | `sync` and `create-pr` with a `pre_push_cmd`, and `env start`. They run for minutes, and the agent reads their output. This needs a job id and a log stream, on the WebSocket pattern the transcripts use. |

The wire task is a projection of the notebook row: `branch` is the default branch when the row
has none, and `log` is a list of entries. So `task read` prints the server's own rendering,
`GET /api/tasks/{project}/{id}/markdown`, not a rebuild from the wire. `task show` prints the
default branch.

A rule the domain owns stays on the server. The status reply carries the follower that
`mael task next --run` would start, so the CLI prints it and does not apply the rule itself.

## The package split

After classes A and B move, the CLI uses `mael_domain` for three things only:

| Package | Holds | For |
|---|---|---|
| git helpers | rebase, squash, worktree reads | class D |
| integrations | GitHub, Linear, Sentry, UptimeRobot, Slack | class D |
| bootstrap | env and port allocation, install-time migrate | class C |

Then `task`, `task_table`, `task_actions`, `task_launch`, `comms`, `comm_store`, `landing*`,
`agent_store`, `agent_cost`, `worktree_close`, `worktree_trash`, `env` and `state_db` are the
server's alone.

## Migration order

1. **Class A.** The routes exist, and agents use these commands most: `task status done` and
   `task show`. The pilot did four of them.
2. **Class B, by agent use.** `task next --run`, `sync`, then `create-pr`. The last two wait
   for the streamed long command.
3. **The package split.** Move the wire types out of `mael_domain`, then split the three
   packages above.

## Testing a thin command

Follow `tests/test_cli_over_api.py`: a real `build_app` on loopback, with the client pointed at
it. The fixtures are in `tests/conftest.py`, and `tests/test_task_create_over_api.py` uses the
same ones. `cli/tests/test_orchestrator_client.py` covers each failure against a stub server.
