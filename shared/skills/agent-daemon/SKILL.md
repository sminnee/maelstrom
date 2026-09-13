---
name: agent-daemon
description: The markers a daemon-driven agent writes to show a document, a picture, or what it is doing. Use when showing the user a file, a draft, a screenshot, or reporting progress.
---

# Showing things to the user

You run under the maelstrom agent daemon. Four markers in the text of an ordinary message reach
the orchestrator, which reads them and acts. Every marker is cut from the message the user reads,
so the same words never appear twice.

A subagent's markers stay as text: only a top-level agent can show anything.

## What you are doing now

```
<note>Rebasing onto main, then re-running the failing port test</note>
```

Replaces the summary on the agent's card. Write one when the shape of your work changes, not for
each step. The latest note wins, and a message with no note leaves the standing one alone.

## A document

Two forms. Use `<doc-content>` for markdown you are writing now, and `<doc-file>` for files that
exist:

```
<doc-content kind="other" title="Changelog draft">
## 1.4.0
- the body, inline
</doc-content>

<doc-file kind="tasks" filename=".drafts/first.md, .drafts/next.md" title="Iteration 1">
```

| Attribute | What it does |
|---|---|
| `kind` | One of `plan`, `tasks`, `pr`, `review`, `other`. An unrecognised kind reads as `other`. |
| `title` | What the tab is called. Defaults to the first filename, then to the kind. |
| `filename` | `<doc-file>` only. Comma-separated; the whole set opens as **one** document, in the order given. |
| `review` | `review="true"` puts the document in front of the user for approval and raises an attention item. Without it the document is a draft, and blocks nothing. |

A filename resolves inside your worktree. An absolute path, or one climbing out with `..`, is
refused before anything is read. A file that cannot be read still opens a document, saying so.

## A picture

```
<image src="docs/shot.png" alt="The failing dialog">
```

Drawn where you wrote it, in the flow of the message, so it is not a document. `src` follows the
same worktree rule as `filename`. `alt` defaults to the filename. A file that may not be shown
leaves prose saying so, never a broken picture.
