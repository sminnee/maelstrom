# Task documents: one owner, every version, links by fragment

Plans, verifications and SARIF scans are one thing: a **Task document**. Each one has one
owning task, keeps every version, and any other task can link it. This replaces the one-row-per-path
attached document and the unmerged Report store of PR #558. The terms are in `CONTEXT.md`.

## The decisions

| Decision | Choice | Why |
|---|---|---|
| Owner | One task owns a document. Its name is unique within that task. | A document comes out of one piece of work. The owner's link is implicit. |
| Reference | `<project>:<task-id>:<name>[@v<n>][#<fragment>]` | See "Why colons". |
| Kinds | `markdown` and `sarif` | A plan and a verification are markdown. A scan is SARIF. A later kind needs a parser, not a schema change. |
| Versions | Every version is kept. A reference reads the latest unless it names `@v<n>`. | A reference that names `@v1` still reads that text after later versions. |
| Fragment | A markdown heading slug, as GitHub makes it, or a SARIF **Finding key** | A link can name one section or one finding. |
| Links | Any task links a document, or one fragment of it, never one version. The fragment must be in the latest version. A link is deleted, not archived. | A task that fixes one finding links that finding. |
| Archive | A task archives only the documents it owns. A request from no task, such as the user's, archives any. An archived document stays readable, takes versions, and keeps its links. | The owner says when its document is done. A linking task must not hide it from the others. |
| Plans | A task has one `plan` document. Each approved plan is its next version, and a hand add may not use the name. | A re-planned task keeps its earlier plans as history. |

## Storage

The documents are in the **State database**. The `task_attachments` table grew into the document
row. `task_attachment_versions` holds the text, and `task_attachment_links` holds the links. The
tables keep the old name because their ladder is append-only. A new ladder would start again from
nothing, and lose the rows.

Media stays in the task's **Bucket**. A version's body refers to it by a `{{MAEL_TASK_DIR}}`
token.

## Why colons

A reference with `/` reads as a file path, and a document name often is one:
`.drafts/verification.md`. With `:` as the separator, a name can hold `/`. So `:`, `@` and `#`
are refused in a document name and in a project name. A task id already refuses them.

## Considered options

- **A Report store of files in the notebook (#558).** Each scan was a directory, and tasks held
  opaque reference strings. It covered SARIF only, and nothing listed which tasks pointed at a
  finding.
- **A table of findings.** It would need a state for each finding. The findings are parsed from the
  stored SARIF each time instead.
