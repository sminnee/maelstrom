# DAST scope template

Write `docs/security/dast-scope.md` with these headings, in this order. It is the rules of engagement for each ZAP run. [zap-plan.md](zap-plan.md) turns it into the plan.

```markdown
# DAST scope

## Target

The worktree's own dev environment only. The skill resolves the URL from the worktree `.env`, and
`target-guard.sh` refuses every other host and port. Never scan a shared, staging, or production
host.

| Service | Port env var | Front end |
|---|---|---|
| <mael service> | <NAME_PORT> | server-rendered / SPA |

## Test accounts

| Role | Auth method | Credential env vars | Logged-in indicator |
|---|---|---|---|
| <role> | form / json / browser / token | ZAP_<ROLE>_USER, ZAP_<ROLE>_PASS or ZAP_<ROLE>_TOKEN | <text or URL that shows a session> |

Values come from the developer's environment. Never write them in a committed file.

## API definitions

<path or URL of each OpenAPI or GraphQL schema, or "None.">

## Do not scan actively

| Path regex | Reason |
|---|---|
| <regex> | logout / deletes the account / sends email / charges a payment |

## Dev-server paths

<the paths the dev server serves its own tooling on, which are not the app; see zap-plan.md>

## Budget

- Spider: <minutes>
- Active scan: <minutes> in total, <minutes> for each rule
```
