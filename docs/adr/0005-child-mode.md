---
status: accepted
---

# Child mode: a server with a parent holds no task state

A server with `parent:` in its config is in **Child mode**. It holds no task, comm or document
state, has no UI, and sends every state call to its parent's API. This reverses "The per-launch
parent" in `docs/dev/remote-hosts.md`, where one server runs agents for several parents and its own
direct agents at the same time.

## Why

Delegated documents showed the cost of a mixed server. Each document rule had to ask which
**Task home** the agent had. A code path that forgot to ask wrote the child's own tables, and the
parent then showed a second copy of the same document. A server that has no local tables for that
state cannot write them by mistake.

## The decisions

| Decision | Choice |
|---|---|
| What makes a child | `parent:` in config. One fact for the whole server, read at start. |
| State models | The server builds a **tasks model**, a **comms model** and a **docs model** at start. A child builds only their parent implementations. It opens no local task, comm or document table for writing. A parent builds the local implementations, which write the store. |
| Own tasks | A child runs no direct agent on a task of its own. It refuses that launch. |
| UI | A child is headless. It serves no UI bundle. Every user interaction goes through the parent's UI. |
| Routes | A child serves only what two callers need. The parent merges and forwards agents, worktrees, files, documents, attention, gate jobs and operations. The child's own agents use the CLI for env, worktrees and agents. A child removes the task, comm and document-store routes, which could write local tables. |
| CLI | On a child, the CLI sends task, comm and document calls to the parent's API. It finds the parent from `parent:` in config and from `MAEL_PARENT_URL`. It never goes through the child's server. |

## Considered options

- **Per-agent homes on a mixed server.** This is the `TaskHomes` design: each agent has a home, and
  a server runs direct and delegated agents side by side. Every rule then asks "which home is this
  agent's?". A delegated code path can still reach the local tables. Rejected.
- **A child UI that shows the parent's tasks through delegating models.** The child's world would
  have to follow the parent's tasks and comms. Rejected for now. The models seam leaves room for
  it later.

## Consequences

- A host that runs work for a parent cannot also hold its own tasks. To use a machine both ways,
  run two servers.
- While a delegated agent lives, the file in its worktree is the live copy of a document. The
  parent holds the only record. See "A delegated agent's document on the parent" in
  `docs/dev/orchestrator-server.md`.
