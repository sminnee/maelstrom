# Triage

Use this file in step 6 of `/security-test`. Its input is `security-reports/zap-<ts>/report.json`, the run log, and the current `claude-security` report from step 1. Its output is `.drafts/security-review.md`.

1. **Group.** Read every entry in `site[]`, not only the first. For each alert, keep `pluginid`, `alert`, `riskdesc`, and each instance's `uri`, `method`, `param`, `attack`, and `evidence`. Make one row for each rule and route. Join instances that differ only in an id or a query value.
2. **Map to code.** Find the handler of each route, and the middleware or server config that sets the headers. Cite `file:line`.
3. **Reproduce.** Reproduce each High and Medium row with `curl -sk`, as the role that found it. Send the `attack` value, then a harmless value, and compare the two responses.
   - The attack has an effect: the row is confirmed.
   - Both responses are the same: the row is a false positive. Record the two commands.
   - Example: ZAP reported Path Traversal on `?v=` of Vite's `/node_modules/.vite/deps/*.js`. `?v=/etc/passwd` returned the same module, so the server ignored the parameter.
4. **Dev-server alerts.** Header alerts (CSP, HSTS, X-Frame-Options, X-Content-Type-Options) on a dev server show the dev config. Find the production server or proxy config. When it sets the header, mark the row "dev only". When it does not, the row is real.
5. **Access control.** Each `requestor` warning in the log is a row: "Expected 403, received 200" is a broken access check until the code proves otherwise. Compare the URLs each role's spider found. A URL that only a higher role should reach is a row.
6. **Static findings.** Match each `claude-security` finding to the rows by sink, file, and route. Mark a finding "confirmed dynamically" when a reproduced row reaches its sink. Mark it "not reached" when no request reached its entry point. Do not mark it "refuted" from a scan alone.
7. **Rank.** Order the rows by the top risks in `docs/security/requirements.md`, then by ZAP risk. Drop a row that matches an accepted risk, and list it in one line at the end.

## Review document

Write `.drafts/security-review.md` in this shape:

```markdown
# Security test — <project> at <short sha>

Target: <url>. Plan: security/zap/plan.yaml. Report: security-reports/zap-<ts>/report.html.
Exit: <0 | 2 | 1>. Static report: CLAUDE-SECURITY-<ts>/ (or "none").

## <Top risk 1>

| Finding | State | Evidence | Recommendation | Effort |
|---|---|---|---|---|
| Missing CSP | ✗ dev and production (`deploy/nginx.conf:12` sets none) | ZAP 10038 on `/`; curl -skI | add a CSP in nginx | S |
| Path traversal `?v=` | false positive | same body for attack and harmless value | add alertFilter | — |

## Static findings

| claude-security id | Title | Dynamic result |
|---|---|---|

## Accepted risks matched

<one line>
```

State is one of: ✗ confirmed, dev only, false positive, not reproduced. Effort is S, M, or L. Every row gets a Recommendation, so the user can choose fix, accept, or false positive for it.
