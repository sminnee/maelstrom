# Requirements template

Write `docs/security/requirements.md` with these headings, in this order. Keep each heading, also when its section is empty: write "None." Cite `file:line` for each claim that comes from the code.

```markdown
# Security requirements

Last reviewed: <YYYY-MM-DD> at <short sha>.

## Assets and data classes

| Data | Class | Where stored | Harm if leaked or changed |
|---|---|---|---|

## Top risks

1. <risk> — <why it ranks here>

## User management

### Roles

| Role | Can | Cannot | Test account env vars |
|---|---|---|---|

### Authentication

<method, MFA, password policy, password reset>

### Sessions

<where kept, cookie flags, lifetime, logout>

### Tenancy

<tenant boundary, and where each lookup is scoped>

### Administration

<who can manage users and roles>

## Trust boundaries

<each boundary: the browser, each external API, each queue or webhook, and what is validated there>

## Abuse cases

- <risk>: <attacker steps>

## Compliance

<each rule and the controls it adds>

## Accepted risks

| Risk | Reason | Accepted on | Review by |
|---|---|---|---|

## Out of scope

- <item> — <reason>
```
