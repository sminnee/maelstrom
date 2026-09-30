# The cmux package

How maelstrom drives cmux from code. For the shell-level `cmux` CLI — opening a browser pane,
sending a command to a terminal — use the `cmux` skill instead.

`lib/domain/src/mael_domain/cmux/` has four layers. Each layer calls only the one below it.

| Layer | File | What it holds |
|---|---|---|
| Transport | `client.py` | `CmuxClient.run(*args) -> CmuxResult`, `SubprocessCmuxClient`, `current_client`, and the timeout. |
| API | `api.py` | `CmuxApi`: one typed method per cmux command. `CliCmuxApi` builds the argv and parses the reply. `FakeCmux` is the in-memory cmux. |
| Mechanics | `model.py` | `CmuxLayout`: idempotent changes to **one** named workspace. |
| Policy | `mael_layout.py` | `MaelCmux` and `WorktreeWorkspace`: the workspace name, the pane convention, and each intent. |

The API and the mechanics know no maelstrom concepts. Only the policy layer knows the
`{project}-{worktree}` name and the pane 0/1/2 convention. CLI call sites and the orchestrator
use the policy layer.

A read across many workspaces is not a change to one workspace, so it is not in `CmuxLayout`.
`MaelCmux.terminal_urls` reads the API directly.

## The API

`CliCmuxApi` reads with `--json --id-format both`. The reply gives each entity a ref, such as
`pane:3`, and a UUID. A command takes the ref. A deep link names the UUID. No reply text is
parsed with a regex. A browser URL is the exception: only `browser get-url` gives it.

Writes keep their text replies. `CmuxResult.ok` and `CmuxResult.ref()` parse them. A failed
command gives `[]`, `None` or `False`, and never raises.

## The fake

Every cmux test uses `FakeCmux`. It holds workspaces, then panes, then tabs, and answers every
`CmuxApi` method from that state. Tests assert the resulting state, not the argv:

```python
cmux = FakeCmux().with_workspace("myproject-alpha", [["Claude"], ["Terminal"]])
MaelCmux(cmux).worktree("myproject", "alpha", "/wt").open_for_shell(install_cmd="npm i")
assert cmux.tabs("myproject-alpha") == [["Claude"], ["Terminal", "Terminal"]]
```

It behaves as real cmux does where the layers depend on it:

- A new workspace, and a split, start with one terminal.
- Closing a pane's last tab removes the pane.
- `workspace=None` names the current workspace: the first one made, unless `current=True` says
  otherwise.
- A surface outside the named workspace is not found.

`sent` logs `(surface ref, text)` for each text typed into a terminal. `focused` logs each ref
that a select or focus command names. `tabs`, `texts`, `workspace_ref`, `surface_ref` and
`pane_link` read the state back. The suites get a `fake_cmux` fixture that makes
`MaelCmux.current()` and `MaelCmux.for_caller()` answer over one. The fixture puts the caller in a
pane: a test that sets `MAEL_HARNESS_TYPE=daemon` or clears `CMUX_WORKSPACE_ID` takes it out.

`RecordingCmuxClient` remains for the API's own parsing tests in `test_cmux_api.py`.

## Partial and idempotent verbs

`CmuxLayout`'s verbs leave everything they do not own alone. Each `ensure_*` asserts that *at
least one* of an entity exists, and creates it only if none does. It touches its own subset and
leaves every other pane, tab, and browser the user opened undisturbed.

`add_*` is the explicit "add another" operation. `ensure_absent_*` is the removal dual.

## Outside cmux

A call site gets its `MaelCmux` from one of two guards. It checks for `None` once. After that,
every method degrades silently.

| Guard | Returns `None` when | Use it for |
|---|---|---|
| `MaelCmux.current()` | No cmux app answers on the socket | Placement a person asked for, and acts on a named workspace: `mael task run` from a terminal, `mael add`, `mael close`, the orchestrator's terminal links |
| `MaelCmux.for_caller()` | The caller is not in a cmux pane | Side effects nobody asked cmux for: the PR browser, the app browser, hiding the app browser |

`current()` does not tell you the caller is in cmux. The socket path falls back to
`~/.local/state/cmux/cmux.sock`, so every process on the machine reaches a running app.

`for_caller()` needs three things. `CMUX_WORKSPACE_ID` is set, `MAEL_HARNESS_TYPE` is not
`daemon`, and the socket answers. The browser verbs pass no `--workspace`, so cmux uses
`CMUX_WORKSPACE_ID`. A caller without it would get the focused workspace, which is often another
worktree.

A driven agent never gets a cmux side effect, and it places a pane only when it passes `--cli`.
The agent daemon removes the `CMUX_` variables of its own pane from its agents' environment. It
keeps `CMUX_SOCKET_PATH` and `CMUX_SOCKET_PASSWORD`, which say where cmux listens. A task that a
driven agent launches starts as a daemon agent with no pane, and the orchestrator shows it. A
driven agent with an attach pane open is no exception: only the daemon knows a client is attached.

## The pane convention

An agent workspace has 3 panes: pane 0 Claude, pane 1 shell, pane 2 browsers. A workspace that
`ensure_terminal` makes has one terminal, in pane 0. `mael_layout.py` is the source of truth —
read it rather than relying on this list.

## Terminal links

cmux 0.64 and later open `cmux://workspace/<workspace uuid>/pane/<pane uuid>` links. The link
focuses a pane that is already open. It cannot make one.

A worktree's terminal link names the pane of the **first terminal tab** in its workspace, in
`list-panels` order. The rule does not use the pane convention. In a workspace that the
orchestrator made, that is its one terminal in pane 0. In an agent workspace, it is the Claude
pane, and cmux shows that pane's selected tab. Either way the link lands on a terminal in the
worktree.

`MaelCmux.terminal_urls` gives the orchestrator's world each worktree's terminal link. It runs
one `list-workspaces`, then one `list-panels` for each matched workspace.
`WorktreeWorkspace.ensure_terminal` returns the link. When the workspace is missing, it first
makes one with one terminal tab in the worktree.

`list-workspaces` and `list-panels` may list only the current cmux window. A worktree workspace in another window
then has no link.

## Timeouts

Every command times out after `COMMAND_TIMEOUT_SECONDS` (10 s) and reads as no answer. A
failure to run the binary also reads as no answer.
