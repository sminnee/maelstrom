# Stacked branches

Maelstrom runs several agents at once, one per worktree, each on its own branch. Stacking lets one
branch build on another instead of on `main`, so two agents working on related code do not fight
over the same files.

## One rebase, one target

**Every rebase maelstrom runs targets the branch's base.** `main` is the base a branch has when it
has none of its own, so an unstacked branch behaves exactly as it always did.

That single rule is the whole mechanism. Maelstrom already rebases automatically — when a worktree
opens, when `mael gh create-pr` pushes, and on every `/watch-pr` loop. Because each of those targets
the base, they maintain a stack for free: a parent's new work cascades into its children with no
extra machinery.

Work is stacked whether or not anyone says so. Merges here are rebases, so every branch is
implicitly based on whatever lands before it. Naming the base resolves that early, one rebase at a
time, instead of late — as one large reconciliation after the parent merges.

## What a base is

A base is **the branch your work is stacked on**. Two facts are stored per branch:

| stored | key | meaning |
|---|---|---|
| base | `branch.<name>.maelBase` | the branch this one is stacked on |
| base tip | `branch.<name>.maelBaseTip` | `origin/<base>`'s SHA at the last successful rebase |

Both live in git config. `git config` without `--worktree` resolves to `$GIT_COMMON_DIR/config`,
so every worktree in a project reads the same bases. `git branch -d` deletes the whole `[branch]`
section, so cleanup costs nothing.

A branch with no stored base rebases onto `origin/main` with the exact command it always used.

## Why the base tip is stored

The base tip is the point this branch's own commits start at. It is the `<upstream>` argument of
`git rebase --onto`:

```bash
git rebase --autostash --onto origin/feat/parent <base_tip>
```

Without it, a plain `git rebase origin/feat/parent` mostly works. Git detects the parent's commits
by patch-id and drops them, even through a squash merge. It fails in one case: **the parent was
amended after the child last re-stacked**. The child then holds a stale copy of a commit whose
patch-id no longer matches, git replays it, and the rebase conflicts.

That case is the normal path here, not an edge case. Every review cycle amends the parent: findings
become `--fixup` commits, and `mael gh create-pr --squash` autosquashes them while rebasing.

| scenario | plain rebase | `--onto <base_tip>` |
|---|---|---|
| base merged as-is | correct | correct |
| unrelated drift on `main` | correct | correct |
| **base amended during review** | **conflict** | correct |

Because the tip must match where the base actually is, `squash_worktree` re-records it on **every**
successful rebase. That is the single write site. A stale tip degrades silently into a naive rebase
that then conflicts on exactly the case the tip exists for.

**Safety guard.** Before rebasing, maelstrom checks `git merge-base --is-ancestor <base_tip> HEAD`.
A tip that is not in this branch's history would make `<upstream>..HEAD` some other range of
commits, and they would vanish with no error. When the check fails, maelstrom falls back to a plain
rebase onto the resolved base.

## Collapse

A base branch does not last. It merges, or it is abandoned. Either way `origin/<base>` disappears
after a prune-fetch, and the child collapses:

- Rebase `--onto origin/main <base_tip>`, which keeps only this branch's own commits.
- Clear the stored base, so the branch is unstacked from then on.

Merged and abandoned are handled identically, and silently. There is nothing for the user to decide.

A sync that skipped the fetch — `mael sync-all`, or the second pass of an autorepair — treats a
missing base ref as **unfetched, not deleted**. The rebase still falls back to `main` so it can run,
but the stored base survives and the collapse waits for the next real sync. Flattening a live stack
for good is worse than collapsing one sync late.

## The stack tip

`maelstrom.stackTip` is one pointer per project: **the branch new worktrees stack on**. It
auto-advances to each new branch, so stacks form a chain rather than a fan.

```bash
mael stack-tip                 # show where new work will stack
mael stack-tip feat/parent     # move it
mael stack-tip main            # reset to the bottom — start unrelated work
```

"Newest open branch" would be the wrong default. It silently stacks fresh work on a branch that was
shelved months ago. That base never merges, so the child never collapses: it carries dead commits in
its PR diff indefinitely and keeps rebasing onto work nobody intends to land.

One explicit pointer avoids the heuristic. It also answers "what will my next worktree stack on?"
readably. Two rules keep it honest:

- **Self-healing.** When the tip's branch is deleted, the tip falls back to `main` and the fallback
  is written back. No `mael add` can base on a dead ref.
- **Stale warning.** When the tip's branch has had no commits for 30 days, `mael add` warns and
  proceeds. It warns rather than blocks because an unattended agent session has nobody to answer a
  judgement call.

Both facts come from one `git for-each-ref` over `refs/remotes/origin`, with no network call.

## GitHub: `gh stack link`, and nothing else

Stacked pull requests still all merge into `main`. The chained base branches are review-time
scaffolding: when the bottom PR merges, GitHub retargets the next one to `main` and rebases it.

`gh stack` is a GitHub CLI extension, not part of `gh` itself. Install it once per machine:

```bash
gh extension install github/gh-stack
```

Without it, a stacked `mael gh create-pr` warns and carries on: the PR is pushed and only the stack
view is missing.

Maelstrom uses **exactly one** `gh stack` command, and only after the branch is pushed and the PR
exists:

```bash
gh stack link feat/grandparent feat/parent feat/child   # bottom to top
```

`link` pushes each branch, adopts existing PRs, creates any that are missing, and chains the PR
bases itself. That correction is the wanted outcome: maelstrom has already arranged the branches
into that chain locally, so `link` declares a state that is already true.

| concern | owner |
|---|---|
| base resolution, cascading rebase, base tip, collapse, force-push | maelstrom |
| stack tip, re-stacking an urgent PR | maelstrom |
| registering the chain on GitHub | `gh stack link` |
| retargeting a child when the bottom merges | GitHub, server-side |

### Why no local `gh stack` command is used

The extension keeps its state in the file `.git/gh-stack`. A linked worktree's git-dir is
`.git/worktrees/<name>/`, and a state *file* is not shared the way config is. Checked from a
maelstrom worktree, `gh stack view` reports `current branch "feat/two" is not part of a stack` for a
branch it reads correctly from the main checkout.

`gh stack rebase` is worse. [Issue #35](https://github.com/github/gh-stack/issues/35) reports that
it **succeeds while doing nothing** when the branch is checked out elsewhere. Silent corruption is
the worst failure mode for an unattended agent.

`gh stack link` is documented as the exception. Its help says it "does not rely on gh-stack local
tracking state". It is meant for people who manage branches with an external tool and still want
GitHub's stacked PRs. Maelstrom is exactly that external tool.

Registration is never fatal. The branch is already pushed and the PR already exists, so a failed
`link` costs the stack view and nothing else. `gh stack` is a public preview; if `link` breaks,
every local behaviour is unaffected.

## Merge order, and the escape hatches

A registered stack merges bottom-up: a PR merges only once everything below it is mergeable. Two
commands get an urgent branch out of the queue:

```bash
mael promote      # move this branch to the bottom; close the stack up behind it
mael eject        # pull this branch onto main; leave the rest alone
```

`promote` re-points this branch onto `main` **and** re-points anything that was based on it onto
this branch's old base. `eject` skips the second half. Both are edits to the stored bases; the
existing rebase machinery does the rest. Run `mael sync` afterwards, here and in any re-pointed
worktree.

## `base` is not `parent`

Two near-identical names with near-opposite meanings. `parent` says "this task is more of the same
work" and groups tasks onto one branch and one PR. `base` says "this branch builds on that branch"
and gives each its own PR. Maelstrom never derives one from the other.

The full comparison lives beside `parent`'s own definition, in
[`tasks.md`](tasks.md#parent-vs-base--near-identical-names-near-opposite-meanings).

## Known limits

- **Force-push races.** A parent and a child both force-push after rebasing, so a child can land on
  a parent tip the parent then replaces. `--force-with-lease` stops one clobbering the other, and
  `mael sync-all` orders parents before children. Full serialisation is out of scope.
- **`--autosquash` does not reach the parent.** Autosquash builds its list from
  `merge-base(HEAD, base)..HEAD`, so a `fixup!` aimed at a commit in the *parent* silently does not
  squash. That commit belongs in the parent's PR, so put the fixup there.
- **`/watch-pr` re-syncs each CI iteration.** With a moving parent, parent and child can ping-pong.
  In practice CI duration bounds it.
- **Deep stacks cascade.** Because the tip auto-advances, a change low in the stack rebases
  everything above it. `mael stack-tip main` is the one-command escape.

## Where the code lives

| concern | module |
|---|---|
| `BaseRef`, `RebasePlan`, `plan_rebase`, `validate_base`, `resolve_stack_tip`, `order_by_stack` | `worktree_model.py` (pure) |
| `BaseStore`, `InMemoryBaseStore`, `GitConfigBaseStore` | `base_store.py` |
| base resolution, the rebase, recording the tip, collapse | `worktree.py` (`squash_worktree`) |
| `gh stack link`, base-relative review diff | `github.py` |
| `--base`, `mael base`, `mael stack-tip`, `mael promote`, `mael eject` | `cli.py` |
