# Worktrees

A worktree isolates code: one branch, one working directory, one set of ports.

## Why worktrees, not clones

Several agents need several checkouts. Full clones would duplicate history and drift apart.
Git worktrees share one object store, so a second checkout costs almost nothing and every
worktree sees the same branches and the same fetched refs.

Maelstrom uses a bare-like layout: one shared `.git`, and each worktree beside it.

```
~/Projects/myproject/
├── .git/                   # shared bare git directory
├── .mael                   # marker: this is a maelstrom project
├── _main/                  # main branch: the reference checkout, not a worktree
├── myproject-alpha/        # feature worktree
│   ├── .maelstrom.yaml     # project config (checked in)
│   ├── .env                # generated ports (gitignored)
│   └── ...
├── myproject-bravo/        # feature worktree, different PORT_BASE
└── myproject-charlie/
```

## `_main`

`_main` holds the main branch. `_main` is the **reference checkout**, not a worktree: it has
no ports and no `.env`. `mael list` does not show it, and `mael add` never recycles it.

Keeping main there leaves every NATO worktree free for feature work. Git allows one
worktree per branch, so a fresh `alpha` is created detached — which makes it a closed
worktree that `mael add <branch>` recycles for the first task.

## Naming

Worktrees take NATO phonetic alphabet names, in order:

> alpha, bravo, charlie, delta, echo, foxtrot, golf, hotel, india, juliet, kilo, lima,
> mike, november, oscar, papa, quebec, romeo, sierra, tango, uniform, victor, whiskey,
> xray, yankee, zulu

They are **not** named after branches. A worktree is a durable **slot** that outlives the
branch it currently holds, which is why it is never named after one. The folder, the name and
the ports of `myproject-bravo` stay put while branches come and go through it.

Target one by name or shortcode:

```bash
mael env start myproject.bravo
mael env start myproject.b        # shortcode
mael env start                    # detected from the current directory
```

## Creating

```bash
mael add feature/avatar-upload    # branch + worktree, recycling a closed slot if free
mael add                          # a fresh worktree on main, never recycled
mael add feature/x --no-recycle   # force a new slot
mael add feature/x --open         # open the editor instead of a Claude session
```

`mael add` fetches, creates the branch from `origin/main`, allocates ports, writes `.env`,
and launches a Claude session. Alpha is created for you by `mael add-project`.

## Recycling

Recycling is why the naming works. `mael close` resets a worktree to main but keeps
everything else, so the slot is reusable. The next `mael add <branch>` prefers a closed
slot over creating a new one.

This keeps the number of directories bounded and, more usefully, **keeps ports stable**.
Bravo's frontend is on the same port this week as last week, so bookmarks and local config
keep working.

## Close vs remove

This is the distinction that matters:

| | `mael close` | `mael remove` |
|---|---|---|
| The folder | Kept | Deleted |
| The NATO name | Kept | Freed |
| The port allocation | Kept | Freed |
| The branch | Kept | Kept |
| Recyclable afterwards | Yes | Not applicable |

**Close preserves. Remove deletes.** Close is the normal end of a piece of work. Remove is
for when you want the slot gone.

### Close

```bash
mael close                # this worktree
mael close myproject.b    # a named one
mael close --wait         # wait for the PR to merge first
```

Close, in order:

1. Sync against `origin/main` (rebase).
2. Check there are no uncommitted changes.
3. Check there are no unmerged commits.
4. Check out main.

It refuses at step 2 or 3 rather than losing work:

```
Error: Worktree 'bravo' has uncommitted changes.
Error: Worktree 'bravo' has commits not merged to main.
```

With `--wait`, close watches the worktree's pull request and only closes once it has
merged. If the PR closes unmerged, or its CI fails, close raises an error instead. Waiting
is bounded by `--timeout` (default 3600s), polling every `--interval` seconds (default 30).

### `close --force` discards nothing

`--force` frees a slot whose work is incomplete. It is **not** a destructive flag:

- An in-progress sync is aborted, not left mid-rebase.
- Uncommitted and untracked changes are **committed** onto the branch as
  `wip: uncommitted changes`, so they ride along and reappear when the branch is reopened.
- The branch and its pull request are never deleted.
- Maelstrom creates a **"Reopen <branch>" task**, so the work is not forgotten.

```bash
mael close --force
```

Use it when priorities change mid-task and you need the worktree back.

### Remove

```bash
mael remove myproject.bravo
mael remove myproject.b myproject.c
mael remove myproject.b -f        # skip the dirty-file confirmation
```

Remove deletes the worktree directory and frees its name and ports. It prompts when there
are modified or untracked files. `mael rm` is an alias.

## Keeping worktrees current

```bash
mael sync                  # rebase this worktree onto origin/main
mael sync --squash         # autosquash fixup! commits while rebasing
mael sync --abort          # on conflict, abort and restore rather than stopping mid-rebase
mael sync --close          # if the branch is empty after rebasing, delete it and close
mael sync-all              # every worktree in the project
```

`--abort` is worth knowing: without it, a conflicting rebase leaves the worktree
mid-operation for you to resolve. With it, the worktree returns to its prior state.

## Tidying branches

```bash
mael tidy-branches
```

For each feature branch that is not checked out in a worktree: pull, rebase onto
`origin/main`, delete it if it has merged, force-push it if it has not. Branches checked out
in a worktree are skipped. A conflicting rebase aborts and skips that branch.

## Health

```bash
mael doctor
```

Checks and repairs the project layout: the `.mael` marker, the bare repository, the fetch
refspec, local main against origin, the origin remote, main being checked out in `_main`,
stale worktree registrations, port allocations, `.env` markers, and the permissions on
files holding secrets. See [troubleshooting.md](troubleshooting.md).

## See also

- [Dev environments](dev-environments.md) — the ports each worktree gets.
- [CLI reference](../reference/cli.md) — every flag.
