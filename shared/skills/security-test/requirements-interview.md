# Requirements interview

Use this file in step 2 of `/security-test`. The output fills [requirements-template.md](requirements-template.md) and [dast-scope-template.md](dast-scope-template.md).

Rules:

- Read the code before each question. Propose an answer from the code, with the `file:line` it came from, and ask the user to confirm or correct it.
- Ask one question at a time. Use `AskUserQuestion` when the answer is a choice. Push back, as `grilling` does, when an answer contradicts the code.
- When an existing `requirements.md` answers a question, show its answer and ask only whether it is still true.
- Ask the sections in this order. The earlier sections set the risk ranking for the later ones.

## 1. Data and assets

- Which data does the app store or pass through? Classify each: credentials, personal data, payment data, health data, secrets of other systems, business data, public data.
- Which data would cause the most harm if it leaked, changed, or became unavailable?
- Which external systems does the app hold credentials for?

## 2. Actors and roles

- Which roles exist? Propose them from the role model, the permission checks, and the admin routes.
- Is there an anonymous role? Which routes does it reach?
- Is the app multi-tenant? What is the tenant boundary: an organisation, a team, or a user?
- Which roles can create, change, or remove other users?

## 3. Authentication and sessions

- How does a user log in: password form, JSON API, OAuth or OIDC, magic link, API token, or an external identity provider?
- Is MFA available or required? For which roles?
- How does password reset work? Does the reset token expire, and is it single-use?
- Where is the session kept: a cookie, a bearer token, or local storage? Which cookie flags are set?
- How long does a session live? Does logout end it on the server?

## 4. Authorisation and tenancy

- Where are permission checks made: a middleware, a decorator, each handler, or the query layer?
- Which object ids appear in URLs or request bodies? Is each lookup scoped to the user or the tenant?
- Which routes are admin-only? Which routes change data across tenants?

## 5. Top risks

- Propose the five most likely and most harmful risks for this app, from sections 1–4 and from the `claude-security` findings. Rank them.
- Ask the user to reorder, add, or remove risks. This ranking sets the triage order.

## 6. Abuse cases

- For each top risk, what does an attacker do, step by step? Write one line per abuse case.
- Which actions are dangerous to repeat: send an email or SMS, charge a payment, call a paid API, delete data? These paths go in the no-active-scan list of `dast-scope.md`.

## 7. Compliance

- Which rules apply: GDPR, the NZ Privacy Act, PCI DSS, HIPAA, SOC 2, or a customer contract? Record only the controls that change the code.

## 8. Accepted risks and scope

- Which known risks does the user accept, and why?
- What is out of scope: third-party services, the infrastructure, the dev-only tooling?
- Which test account does each role use? Record the env var **names** (`ZAP_<ROLE>_USER` and `ZAP_<ROLE>_PASS`, or `ZAP_<ROLE>_TOKEN`), never the values.
- What is the scan budget: the maximum minutes for the active scan?
