# The cmux package

How maelstrom drives cmux from code. For the shell-level `cmux` CLI — opening a browser pane,
sending a command to a terminal — use the `cmux` skill instead.

`src/maelstrom/cmux/` follows the three layers in
[architecture-patterns.md](architecture-patterns.md):

- `client.py` — transport. The `CmuxClient` Protocol, the real `SubprocessCmuxClient`, the
  fake `RecordingCmuxClient`, `CmuxResult` parsing, and `current_client` / `is_cmux_mode`.
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
