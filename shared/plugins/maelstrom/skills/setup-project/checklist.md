# Checklist

The checks the `/setup-project` audit runs. The skill sends one category to each auditor.

## Three rules

**A present-but-broken gate is worse than an absent one.** A broken gate reports success, so nobody looks again. Open each file or step and confirm that it can fail.

**Not every check applies to every project.** Report a gap only where it costs this project something. Where a check does not apply, report `n/a` with the fact that shows it.

**A check with a stated threshold is `n/a` below it.** Where a bullet names a threshold or an `n/a` condition, a project below it is `n/a`, not a gap.

---

## Category: Gates that cannot fail

- **Lint step runs the check variant.** Passes when each lint or format step runs `--check` (or the equivalent), not `--write` or `format`. Trace `npm run <script>` to its definition.
- **Deploy waits for a green build.** Passes when each `workflow_run` deploy has `if: github.event.workflow_run.conclusion == 'success'`.
- **CI installs from the lockfile.** Passes when CI uses `npm ci`, `--frozen-lockfile`, `uv sync --locked`, or the equivalent.
- **Hooks can run.** Passes when every tool a pre-commit hook guards on still exists in the project.
- **Named scripts exist.** Passes when every script that CI or the README names is defined in a manifest or `bin/`.

## Category: CI

- **PR workflow runs the tests.** Passes when a workflow on `pull_request` runs the test suite.
- **Superseded runs stop.** Passes when workflows set `concurrency` with `cancel-in-progress`.
- **Workflows set `permissions:`.** Passes when each workflow declares the access it needs.
- **Gates are separate.** Passes when lint, typecheck, and test run as separate steps or jobs.
- **Deploys are declarative.** Passes when the deployed state is a committed manifest. `n/a` when the project does not deploy.

## Category: Tests

- **Framework configured.** Passes when the test config is committed, not bare defaults.
- **Tests in proportion.** Passes when the count of test files is reasonable for the count of source files.
- **Test config paths exist.** Passes when every directory a test config includes or excludes exists.
- **Specs map to tests.** Passes when behaviour specs carry stable ids and tests reference them, checked in CI. Where no spec discipline exists, this is a recommendation, not a defect.
- **Test layers documented.** Passes when the docs say what belongs in each test layer.

## Category: Dead code

Recommend a tool unless the project is trivially small.

- **Dead-code tool runs in CI.** Passes when CI runs knip, vulture, or `-D warnings` for Rust, and a finding fails the job.
- **Whitelist is not burying findings.** Passes when the whitelist is small and its entries are still real.

## Category: Security and dependencies

- **No committed credentials.** Passes when neither the tree nor `git log --all --diff-filter=A --name-only` holds keys, certs, `.env` files, or tokens.
- **Actions pinned.** Passes when no `uses:` line names a mutable ref such as `@master`.
- **Runtime pinned at the root.** Passes when a pin file (`.nvmrc`, `.python-version`, `rust-toolchain.toml`, `engines`) matches the CI version, and the version is supported.
- **Dependencies get updates.** Passes when Dependabot, Renovate, or a scheduled maintenance workflow exists.
- **Base images supported.** Passes when each Dockerfile `FROM` and CI toolchain is inside its support window.

## Category: Docs

- **README is true.** Passes when the README names this project, describes the current stack, and lists only scripts that exist.
- **Clone to running.** Passes when the setup, test, and build instructions match the manifests.
- **Gates listed.** Passes when the docs list each CI gate and how to run it locally.
- **LICENSE matches.** Passes when a manifest `license` field has a matching file.
- **Architecture documented.** Passes when `docs/` says how the parts fit together. `n/a` for a single-module project.

## Category: Agent config

- **`CLAUDE.md` at the root.** Passes when it names the commands, the docs, and the architecture.
- **Conventions written down.** Passes when `docs/review/coding-standards.md` or `.claude/review-guides/<language>.md` carry real rules.
- **Permissions allowlist.** Passes when `.claude/settings.json` allows the routine commands the project needs.
- **Env files handled.** Passes when `.env` is ignored, an example file lists each variable the project reads, and `.claude/CLAUDE.local.md` is ignored.
