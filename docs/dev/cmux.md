# The cmux package

How maelstrom drives cmux from code. For the shell-level `cmux` CLI — opening a browser pane,
sending a command to a terminal — use the `cmux` skill instead.

Maelstrom uses cmux for three things only:

- The orchestrator's `cmux` link makes a worktree's workspace with one terminal, or links to the
  terminal it has.
- The orchestrator's world shows each worktree's terminal link.
- Closing or trashing a worktree closes its workspace.

`lib/domain/src/mael_domain/cmux/` has three layers. Each layer calls only the one below it.

| Layer | File | What it holds |
|---|---|---|
| Transport | `client.py` | `CmuxClient.run(*args) -> CmuxResult`, `SubprocessCmuxClient`, `current_client`, and the timeout. |
| API | `api.py` | `CmuxApi`: one typed method per cmux command. `CliCmuxApi` builds the argv and parses the reply. `FakeCmux` is the in-memory cmux. |
| Policy | `mael_layout.py` | `MaelCmux` and `WorktreeWorkspace`: the workspace name and the three uses. |

The API knows no maelstrom concepts. Only the policy layer knows the `{project}-{worktree}`
name. The orchestrator and the worktree close and trash steps use the policy layer.

## The API

`CliCmuxApi` reads with `--json --id-format both`. The reply gives each entity a ref, such as
`pane:3`, and a UUID. A command takes the ref. A deep link names the UUID. No reply text is
parsed with a regex.

Writes keep their text replies. `CmuxResult.ok` and `CmuxResult.ref()` parse them. A failed
command gives `[]`, `None` or `False`, and never raises.

## The fake

Every cmux test uses `FakeCmux`. It holds workspaces, then panes, then tabs, and answers every
`CmuxApi` method from that state. Tests assert the resulting state, not the argv:

```python
cmux = FakeCmux()
url = MaelCmux(cmux).worktree("myproject", "alpha", "/wt").ensure_terminal()
assert cmux.tabs("myproject-alpha") == [["Terminal"]]
assert url == cmux.pane_link("myproject-alpha", 0)
```

A new workspace starts with one terminal, as in real cmux. `commands` logs the command each new
workspace's terminal ran. `tabs` and `pane_link` read the state back. The suites get a
`fake_cmux` fixture that makes `MaelCmux.current()` answer over one.

`RecordingCmuxClient` remains for the API's own parsing tests in `test_cmux_api.py`.

## Outside cmux

`MaelCmux.current()` returns `None` when no cmux app answers on the socket. A call site checks
for `None` once. After that, every method degrades silently. With cmux down, the orchestrator refuses the terminal command with "cmux could not make the
terminal".

`current()` does not tell you the caller is in cmux. The socket path falls back to
`~/.local/state/cmux/cmux.sock`, so every process on the machine reaches a running app.

The agent daemon removes the `CMUX_` variables of its own pane from its agents' environment. It
keeps `CMUX_SOCKET_PATH` and `CMUX_SOCKET_PASSWORD`, which say where cmux listens.

## Terminal links

cmux 0.64 and later open `cmux://workspace/<workspace uuid>/pane/<pane uuid>` links. The link
focuses a pane that is already open. It cannot make one.

A worktree's terminal link names the pane of the **first terminal tab** in its workspace, in
`list-panels` order. In a workspace that the orchestrator made, that is its one terminal. In a
workspace the user laid out, the link still lands on a terminal in the worktree.

`MaelCmux.terminal_urls` gives the orchestrator's world each worktree's terminal link. It runs
one `list-workspaces`, then one `list-panels` for each matched workspace.
`WorktreeWorkspace.ensure_terminal` returns the link. When the workspace is missing, it first
makes one with one terminal tab in the worktree. The terminal runs only its `cd`.

`list-workspaces` and `list-panels` may list only the current cmux window. A worktree workspace
in another window then has no link.

## Timeouts

Every command times out after `COMMAND_TIMEOUT_SECONDS` (10 s) and reads as no answer. A
failure to run the binary also reads as no answer.
