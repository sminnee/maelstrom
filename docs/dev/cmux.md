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
`MaelCmux.current()` answer over one.

`RecordingCmuxClient` remains for the API's own parsing tests in `test_cmux_api.py`.

## Partial and idempotent verbs

`CmuxLayout`'s verbs leave everything they do not own alone. Each `ensure_*` asserts that *at
least one* of an entity exists, and creates it only if none does. It touches its own subset and
leaves every other pane, tab, and browser the user opened undisturbed.

`add_*` is the explicit "add another" operation. `ensure_absent_*` is the removal dual.

## Outside cmux

`current_client()` and `MaelCmux.current()` return `None` outside cmux.
A call site checks for `None` once. After that, every method degrades silently.

## The pane convention

maelstrom uses a 3-pane layout per worktree workspace: pane 0 Claude, pane 1 shell, pane 2
browsers. `mael_layout.py` is the source of truth — read it rather than relying on this list.

## Timeouts

Every command times out after `COMMAND_TIMEOUT_SECONDS` (10 s) and reads as no answer. A
failure to run the binary also reads as no answer.
