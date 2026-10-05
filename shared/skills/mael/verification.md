# Verification

Write a verification when the work has a result the user can see. Write it after you commit the implementation.

1. Write `.drafts/verification.md`. Say what you checked and how.
2. Put each screenshot and Playwright video in the body as a markdown image ref.
3. When the project runs Ladle, link a story as plain markdown: `<scheme>://<host>:<Ladle port>/?story=<title-id>--<export>&mode=preview`. Take `<scheme>` and `<host>` from the app URL in the Environment section of `CLAUDE.local.md`. When there is no app URL, use `DEV_SCHEME` and `DEV_HOST` from `.env`. Take `<Ladle port>` from the worktree's `.env`.
4. Show it with `<doc-file kind="verification" filename=".drafts/verification.md" title="...">`.

When a review fix changes what the verification shows, update the file and write the tag again.
