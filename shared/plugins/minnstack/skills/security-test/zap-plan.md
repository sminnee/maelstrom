# ZAP plan

Use this file in step 3 of `/security-test`. It turns `docs/security/dast-scope.md` into `security/zap/plan.yaml`, a ZAP Automation Framework plan. The job reference is at https://www.zaproxy.org/docs/automate/automation-framework/.

## Variables

The plan reads `${NAME}` from the container environment. Use only these:

- `ZAP_TARGET` — the guard's output: `scheme://host:port`, with no path.
- `ZAP_REPORT_DIR` — `/zap/wrk/out`, the report directory mount.

The plan is mounted at `/zap/wrk/plan/`, and a schema file at `/zap/wrk/schema/`. Nothing else of the worktree is in the container.
- `ZAP_<ROLE>_USER`, `ZAP_<ROLE>_PASS`, or `ZAP_<ROLE>_TOKEN` — one set for each role in the scope.

`alertFilter` checks its `url` regex before it substitutes variables, so `${ZAP_TARGET}` there fails the plan with "Invalid URL regex". Write the filter URL as a plain regex, such as `.*/node_modules/.*`.

## Contexts and users

Write each context's `includePaths` as `${ZAP_TARGET}(/.*)?`. With `${ZAP_TARGET}.*`, port 3590 also includes port 35901.


Make one context for each role, with one user. Then spider and scan each context as its user. Each role's spider shows which URLs that role reaches. Triage compares them.

Add an `anonymous` context with no authentication when the app has public routes.

## Authentication

Choose the method from the login code that step 1 found:

| Login | `authentication.method` | Notes |
|---|---|---|
| HTML form that posts and sets a cookie | `form` | `loginRequestBody: username={%username%}&password={%password%}` |
| JSON POST that sets a cookie or returns a token | `json` | `loginRequestBody: '{"email":"{%username%}","password":"{%password%}"}'` |
| SPA, OAuth or OIDC redirect, or a CSRF token on the form | `browser` | `loginPageUrl`, `loginPageWait: 5`, `browserId: firefox-headless` |
| Long-lived API token | none | Add the header with a `replacer` job and `${ZAP_<ROLE>_TOKEN}` |

Set `sessionManagement.method`:

- `cookie` for a session cookie.
- `headers` for a bearer token that the login returns. Give the header in `parameters`, such as `Authorization: Bearer {%json:token%}`, where `token` is the JSON path of the token in the login response. Without it, the scan runs as anonymous.
- `autodetect` with `browser` authentication.

Set `verification`:

- `method: response` with `loggedInRegex` when each authenticated page shows the indicator from `dast-scope.md`, such as the user menu.
- `method: poll` with `pollUrl` (such as `/api/me`), `pollFrequency: 60`, `pollUnits: requests`, and `loggedInRegex` when pages do not show it.

## Exclusions

Put each "Do not scan actively" regex from `dast-scope.md` in the context `excludePaths`.

Also exclude the dev server's own paths. They are not the app, and they make the spider and the active scan slow. A Vite dev server serves these:

```yaml
- ${ZAP_TARGET}/@vite/.*
- ${ZAP_TARGET}/@fs/.*
- ${ZAP_TARGET}/@id/.*
- ${ZAP_TARGET}/@react-refresh.*
- ${ZAP_TARGET}/node_modules/.*
- ${ZAP_TARGET}/src/.*
```

Without them, `spiderAjax` found 13,641 URLs in a Vite app, and most active-scan time went to module files. For another dev server, read its asset paths from one page's HTML.

## Spiders and imports

- Always run `spider` first. It is fast, and it seeds the others.
- For a SPA, also run `spiderClient`. It drives Firefox headless and finds client-side routes. Use `spiderAjax` only when `spiderClient` finds too few routes.
- When the project has an OpenAPI schema, run `openapi` with `apiFile` or `apiUrl`, `targetUrl: ${ZAP_TARGET}`, and the `context`.
- When it has a GraphQL schema, run `graphql` with `endpoint` and `schemaFile` or `schemaUrl`.

## Access-control checks and static findings

Add a `requestor` job for each:

- Admin-only URL. Send it as the lowest role, with `responseCode: 403` (or 401, or the redirect the app uses).
- Cross-tenant object URL. Send it as a user of another tenant, with the code for "not found" or "forbidden".
- `claude-security` finding with an HTTP entry point. Send the request that reaches the sink, so the active scan and triage see it.

A `responseCode` that does not match is a plan warning, and the run exits 2.

Send API URLs, not SPA page routes. A SPA server answers each page route with `index.html` and status 200, whoever asks.

## Job order

1. `alertFilter` — first, because it only filters alerts raised after it runs.
2. `replacer`, when a role uses a token.
3. `passiveScan-config`, with `maxAlertsPerRule: 10`.
4. `openapi` or `graphql`, when there is a schema.
5. For each context: `spider`, then `spiderClient` for a SPA.
6. `requestor`.
7. `passiveScan-wait`.
8. For each context: `activeScan`, with `maxScanDurationInMins` and `maxRuleDurationInMins` from the scope budget.
9. Three `report` jobs.

## Reports

```yaml
- type: report
  parameters: {template: traditional-html, reportDir: "${ZAP_REPORT_DIR}", reportFile: report.html}
- type: report
  parameters: {template: sarif-json, reportDir: "${ZAP_REPORT_DIR}", reportFile: report.sarif.json}
- type: report
  parameters: {template: traditional-json, reportDir: "${ZAP_REPORT_DIR}", reportFile: report.json}
```

`sarif-json` appends `.json` to a file name that does not end in it.

## How the container reaches the app

- Under `dev_https:`, use the worktree URL as it is: `https://${DEV_HOST}:<port>`. The container resolves the tailnet host, and the certificate is valid there. No `--add-host` is needed with Docker Desktop.
- When the dev server listens only on `localhost`, use `http://host.docker.internal:<port>` as `ZAP_TARGET`. On Linux, add `--add-host host.docker.internal:host-gateway` to `docker run`. `target-guard.sh` accepts this host.
- The spider also finds other ports of the same host, such as the HMR port. They are out of scope and get no alerts. Triage reads every site in `report.json`, not only the first.

## Example

A server-rendered app with form login, two roles, and an OpenAPI schema:

```yaml
env:
  contexts:
    - name: admin
      urls: ["${ZAP_TARGET}"]
      includePaths: ["${ZAP_TARGET}(/.*)?"]
      excludePaths: &exclude
        - ${ZAP_TARGET}/logout.*
        - ${ZAP_TARGET}/account/delete.*
        - ${ZAP_TARGET}/invoices/.*/send
      authentication:
        method: form
        parameters:
          loginPageUrl: ${ZAP_TARGET}/login
          loginRequestUrl: ${ZAP_TARGET}/login
          loginRequestBody: username={%username%}&password={%password%}
        verification:
          method: response
          loggedInRegex: \Qdata-user-menu\E
      sessionManagement:
        method: cookie
      users:
        - name: admin
          credentials:
            username: ${ZAP_ADMIN_USER}
            password: ${ZAP_ADMIN_PASS}
    - name: member
      urls: ["${ZAP_TARGET}"]
      includePaths: ["${ZAP_TARGET}(/.*)?"]
      excludePaths: *exclude
      authentication:
        method: form
        parameters:
          loginPageUrl: ${ZAP_TARGET}/login
          loginRequestUrl: ${ZAP_TARGET}/login
          loginRequestBody: username={%username%}&password={%password%}
        verification:
          method: response
          loggedInRegex: \Qdata-user-menu\E
      sessionManagement:
        method: cookie
      users:
        - name: member
          credentials:
            username: ${ZAP_MEMBER_USER}
            password: ${ZAP_MEMBER_PASS}
  parameters:
    failOnError: true
    failOnWarning: false
    progressToStdout: true

jobs:
  - type: alertFilter
    alertFilters:
      # The CSP report endpoint echoes its JSON body; it is never rendered (checked 2026-10-26).
      - ruleId: 40012
        newRisk: False Positive
        url: .*/csp-report
        urlRegex: true
  - type: passiveScan-config
    parameters: {maxAlertsPerRule: 10}
  - type: openapi
    parameters: {apiFile: /zap/wrk/schema/openapi.yaml, targetUrl: "${ZAP_TARGET}", context: admin}
  - type: spider
    parameters: {context: admin, user: admin, maxDuration: 2}
  - type: spider
    parameters: {context: member, user: member, maxDuration: 2}
  - type: requestor
    parameters: {user: member}
    requests:
      - url: ${ZAP_TARGET}/admin/users
        method: GET
        responseCode: 403
  - type: passiveScan-wait
    parameters: {maxDuration: 5}
  - type: activeScan
    parameters: {context: admin, user: admin, maxRuleDurationInMins: 2, maxScanDurationInMins: 20}
  - type: activeScan
    parameters: {context: member, user: member, maxRuleDurationInMins: 2, maxScanDurationInMins: 20}
  # The three report jobs from "Reports".
```
