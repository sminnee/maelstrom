---
name: security-test
description: Agree a project's security requirements, then scan the worktree's dev environment with OWASP ZAP and triage the alerts. Invoked as `/security-test`.
disable-model-invocation: true
---

# Security test

This skill writes these files in the target project:

| Path | Committed | Content |
|---|---|---|
| `docs/security/requirements.md` | yes | [requirements-template.md](requirements-template.md) |
| `docs/security/dast-scope.md` | yes | [dast-scope-template.md](dast-scope-template.md) |
| `.claude/claude-security-guidance.md` | yes | the marked section from [guidance-template.md](guidance-template.md) |
| `security/zap/plan.yaml` | yes | the ZAP Automation Framework plan |
| `security-reports/zap-<ts>/` | no, gitignored | `report.html`, `report.sarif.json`, `report.json` |
| `.drafts/security-review.md` | no | the triage review |

Do not name an output directory `CLAUDE-SECURITY-*`. The `claude-security` plugin reads each such directory as its own report.

1. **Profile.** When the session is in plan mode, stop and tell the user to relaunch the task with `--mode normal`. Plan mode blocks the files this skill writes.

   Read the framework, routes, auth code, user and role models, OpenAPI or GraphQL schemas, and the `services:` in `.maelstrom.yaml`.
   - Find the newest `CLAUDE-SECURITY-*/` directory. Use its `CLAUDE-SECURITY-RESULTS.jsonl` only when its `CLAUDE-SECURITY-REVISION-<sha12>.json` matches `git rev-parse --short=12 HEAD` and has no `-dirty` suffix.
   - When there is no current report, offer to run `/claude-security` first. Continue without one when the user declines.
2. **Requirements.** When `docs/security/requirements.md` is missing, or the auth code changed after its last commit (`git log -1 --format=%cI -- <path>`), run [requirements-interview.md](requirements-interview.md).
   - Write `requirements.md`, `dast-scope.md`, and the guidance section from their templates.
   - Show the three files in one `<doc-file kind="other" review="true">` tag.
   - Stop until the user approves them.
3. **Plan.** Read [zap-plan.md](zap-plan.md). Write or update `security/zap/plan.yaml` from `dast-scope.md`. Add `security-reports/` to `.gitignore` when it is not there.
4. **Target guard.**
   - Run `mael env start` for each service the scope names.
   - Resolve the URL from the worktree `.env`: `${DEV_SCHEME}://${DEV_HOST}:<port>`, where `<port>` is the value of the scope's Port env var for that service. Give no path and no trailing slash.
   - Pass it through the guard, and use only the guard's output from here on:

     ```bash
     target=$(~/.claude/skills/security-test/target-guard.sh "$url") || exit 1
     ```

     When the guard refuses, stop and show its reason. Never scan another host.
   - For each role with `form`, `json`, or token authentication, log in with `curl` against `$target`, and confirm the logged-in indicator. For a role with `browser` authentication, the run log confirms the login. When a login fails, stop and ask the user to fix the account.
5. **Run.** Docker needs the unsandboxed path. Mount only the plan, read-only, and the report directory. Never mount the worktree root: it holds `.env` and `.git/`.

   ```bash
   ts=$(date +%Y%m%d-%H%M%S); mkdir -p security-reports/zap-$ts
   docker run --rm \
     -v "$PWD/security/zap":/zap/wrk/plan:ro \
     -v "$PWD/security-reports/zap-$ts":/zap/wrk/out:rw \
     -e ZAP_TARGET="$target" -e ZAP_REPORT_DIR=/zap/wrk/out \
     -e ZAP_<ROLE>_USER -e ZAP_<ROLE>_PASS -e ZAP_<ROLE>_TOKEN \
     ghcr.io/zaproxy/zaproxy:2.17.0 zap.sh -cmd -autorun /zap/wrk/plan/plan.yaml
   ```

   - Add `-v "$PWD/<schema>":/zap/wrk/schema/<file>:ro` when the plan imports a schema file.
   - The image is pinned. To move to a new ZAP version, change the tag here and run the plan once before you commit.
   - Pass each credential with `-e NAME` and no value, so it comes from the environment and stays out of the command line.
   - Exit 0: the plan succeeded. Go to step 6.
   - Exit 2: a job warned, such as a `requestor` response code that did not match. Go to step 6. Triage reads each warning as a row.
   - Exit 1: the plan failed. Read the log, fix `plan.yaml`, and run again.
6. **Triage.** Read [triage.md](triage.md). Write `.drafts/security-review.md` and show it with `<doc-file kind="review">`. Stop for the user to choose fix, accept, or false positive for each row.
7. **Record.** For each row:
   - Accept: add it to the accepted risks in `requirements.md`, with the reason, the date, and a review-by date. Then write the guidance section again from [guidance-template.md](guidance-template.md).
   - False positive: add an `alertFilter` rule to the plan, with a comment that gives the reason.
   - Fix: load `planning`. Draft one plan-mode task for each fix row. Each draft names the finding, its `file:line`, and the top risk it falls under. Present the drafts as `planning` says.

   Commit the changed documents and the plan. Do not commit `security-reports/`.
8. **Hand over.** When there are fix drafts: promote them, close this task, launch the head, and end the session, as `planning` says. Otherwise close this task and end the session.
