# Maelstrom merges agent work through a local build queue

Status: accepted, 2026-11-10. The chain after task q944 builds it. See
[the build queue](../dev/build-queue.md) for the mechanics.

## Context

An agent finishes with `mael gh create-pr`. GitHub Actions runs the gates from tangier's
`pipeline.toml`: `lint`, `test`, `web`, `jig`, and the CI-only `e2e`. CI skips a gate whose
content already has a local pass record. The user reviews the PR and clicks Merge in the
orchestrator UI, which merges on GitHub. The `merged` **Landing step** and the agent's
`pr.merged` event come from GitHub.

Three costs follow:

- The human sees the work only after review and present. A redirect at that point wastes the
  tokens of both.
- CI runs on GitHub's runners, which do not have the dev environment that maelstrom already
  builds per worktree.
- The gates have two runners, the agent's worktree and GitHub Actions, with two configurations.

## Decision

A project sets `merge: local` in `.maelstrom.yaml`. The default stays `github`, and PR mode does
not change.

- **Submit is an agent act. Merge is a human act.** `mael merge submit` replaces `create-pr` in
  the agent's finishing flow. In a `merge: github` project it does what `create-pr` does, so an
  agent learns one finishing command. `mael gh create-pr`, `read-pr` and `withdraw-pr` stay as
  legacy commands that ignore `merge:`.
- **Two records: the Merge request and its Queue builds.** A **Merge request** is the task
  branch's request to merge, as a PR is. Each submit updates it, and queues a **Queue build**:
  one gate run of its SHA in a **Build worktree**. The request links its newest Queue build, and
  only that one counts. The status comes from the two. The queue gates and does not merge. The
  Merge button merges a `passed` Merge request.
- **The submitted SHA is the branch head, based on `main`.** `mael merge submit` refuses a
  branch that is not based on the current `origin/main` tip, and a dirty worktree. Merge
  fast-forwards `main` to the SHA, so the commits that were gated are the commits that land.
  There is no squash. The story commits of `/present` land as they are.
- **The server writes both records; the CLI does the git work.** The CLI fetches, checks the
  base and pushes in the agent's worktree, which the server cannot see on a remote host. It then
  calls the server through `OrchestratorClient.for_tasks()`.
- **The queue never changes a branch.** All git work on a branch stays with the agent. The queue
  sends `build.*` **External events** to the Merge request's **Chaperone**: the agent of its
  task. The server resumes that agent when it is not live.
- **Each Merge request is gated against `main` alone.** It is not gated on top of the one
  before it. A human picks the merge order at the Merge button.
- **No rebuild when `main` moves.** A `passed` Merge request whose base is not the `main` tip is
  `stale`. Only a Merge request that a human wants to merge now builds again.
- **A steer review comes before polish.** After the `built` milestone, the agent asks the user
  for a rough steer, then waits. It is a **Checkpoint**, not a **Gate**.
- **Gates have two weights, both from `pipeline.toml`.** The agent runs `@pre-merge --direct`
  before the steer review and before submit. The queue runs `@merge`, with the `depends`
  cascade and `e2e`. There is no second gate list in `.maelstrom.yaml`.
- **Tangier owns how a command runs. Maelstrom owns where.** Ports, `.env`, `install_cmd` and
  services belong to maelstrom, so a **Build worktree** is a maelstrom worktree. It is not in the
  world, and the running Queue build's **Gate lozenge** shows on the Merge request's own node.
  The queue sets `TANGIER_RUNNER_KIND=queue`, so `accept = "kind=queue"` selects its records.
- **Docker is a tangier runner.** `[gate.<name>] container = "<image bucket>"` runs the gate in
  the image from `[image.<bucket>]`, with the checkout mounted.
- **The push to `main` is a fast-forward.** `git push origin <sha>:main`, with no force. A
  rejected push makes the Merge request `stale`. Two servers' queues for one project stay safe with
  no lock.
- **Branch stacking goes.** One branch per task chain, based on `main`.

## Considered options

- **Maelstrom-owned runners.** A job runner in the orchestrator, beside tangier. Rejected: it
  makes a second place that defines a gate, and loses tangier's records and its job slots.
- **Keep a PR as the record.** Open a PR, gate it locally, merge it on GitHub. Rejected: the PR
  holds nothing the task and its documents do not hold, and GitHub's merge rewrites the commits
  that were gated.
- **One record, a "Candidate", per submit.** Rejected: it mixed the request a human merges with
  the gate run, and each new submit replaced the last result. Two records let each rebuild keep
  its result, and keep slot and job fields off the record a human merges.
- **Merge automatically when the gates pass.** Rejected: the final review is the user's last
  view of the work. Merge stays a human act.
- **A chain of Merge requests, each gated on the one before.** The usual merge queue. Rejected:
  one failed or slow request blocks every request after it. A human picks the order at the Merge
  button, so the queue does not need one.
- **The queue rebases or squashes for the agent.** Rejected: a rebase can conflict, and only the
  agent knows the intent of each hunk. A squash discards the story commits.
- **Rebuild every Merge request when `main` moves.** Rejected: with N requests, the builds grow
  with N². Rebuilding on demand grows with the merges, and records make a rebuild cheap.
- **Keep stacking to order the queue.** Rejected: it is the chain of Merge requests above. Nobody
  uses stacking now.

## Consequences

- GitHub becomes a plain git remote for a `merge: local` project. The release workflow stays on
  GitHub Actions.
- The task closes at merge, not at submit. The agent stays live, or is resumed, until then.
- `.drafts/pr.md` becomes the Merge request's description, and the merge review shows it.
- The **Build** phase ends at submit. The **Pre-PR gate** becomes `@pre-merge`.
- The orchestrator token must be allowed to push `main`.
- Tangier must ship gate sets, `TANGIER_RUNNER_KIND`, `--direct` and the container runner before
  a project switches. Until then, the queue runs `tangier gate run --all`.
