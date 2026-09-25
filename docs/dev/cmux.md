# The cmux package

How maelstrom drives cmux from code. For the shell-level `cmux` CLI — opening a browser pane,
sending a command to a terminal — use the `cmux` skill instead.

`lib/domain/src/mael_domain/cmux/` follows the three layers in
[architecture-patterns.md](architecture-patterns.md):

- `client.py` — transport. The `CmuxClient` Protocol, the real `SubprocessCmuxClient`, the
  fake `RecordingCmuxClient`, `CmuxResult` parsing, and `current_client`.
- `model.py` — `CmuxLayout`: pure cmux mechanics over a client.
- `mael_layout.py` — policy. The only layer that knows the `{project}-{worktree}` workspace
  name and the pane 0/1/2 convention. CLI call sites use these functions.

## Partial and idempotent verbs

`CmuxLayout`'s verbs leave everything they do not own alone. Each `ensure_*` asserts that *at
least one* of an entity exists, and creates it only if none does. It touches its own subset and
leaves every other pane, tab, and browser the user opened undisturbed.

`add_*` is the explicit "add another" operation. `ensure_absent_*` is the removal dual.

## Outside cmux

Everything degrades silently: `current_client()` and `CmuxLayout.current()` return `None`.
Call sites do not need a guard.

## The pane convention

maelstrom uses a 3-pane layout per worktree workspace: pane 0 Claude, pane 1 shell, pane 2
browsers. `mael_layout.py` is the source of truth — read it rather than relying on this list.

## Pane ids and deep links

cmux 0.64 and later open `cmux://workspace/<workspace uuid>/pane/<pane uuid>` links. The link
focuses a pane that is already open. It cannot make one.

The ids come from the `--json --id-format uuids` forms of `list-workspaces` and `list-panes`.
The text forms print only short refs such as `pane:3`, which a link cannot use.
`CmuxResult.json()` parses the reply, and `model.workspace_pane_ids` reads the ids.

`mael_layout.worktree_shell_urls` gives the orchestrator's world each worktree's shell pane link.
`mael_layout.create_worktree_terminal` makes the shell pane when it is missing and returns its
link. Neither focuses anything.

`list-workspaces` may list only the current cmux window. A worktree workspace in another window
then has no link.

Every command times out after `COMMAND_TIMEOUT_SECONDS` (10 s) and reads as no answer. The
orchestrator's worktree read calls cmux on every poll, so a wedged cmux must not hold it.
