# Hygiene Checklist

The checks the `/review-project-hygiene` audit runs, grouped by category. The parent skill sends
one category to each sub-agent, together with `auditor-prompt.md` and the project profile.

Each check states **what to check**, **how to check it**, and **what its absence costs**. Within
each category the checks are ordered by frequency of absence multiplied by cost.

## Three rules that frame every check

**A present-but-broken gate is worse than an absent one.** An absent gate is visible — somebody
notices there is no lint step. A broken gate reports success. The CI badge is green and nobody
looks again. Never accept a file or a step as a passing check because of its name. Open it and
confirm it can fail.

**Not every check applies to every project.** This checklist prompts judgement; it is not a form
to complete. Report a gap only where it costs *this* project something. Where a check does not
apply, say why. Do not skip it silently, and do not report it as a defect.

**Each check states its own threshold.** Some checks are near-universal, such as dead-code
detection. Some scale with size, such as architecture fences. Some depend on the kind of project,
such as deploy gating. A check that names its threshold can be declined honestly. A check that
does not names every small project as deficient, which is how a checklist stops being read.

---

## Category: Gates that cannot fail

The highest-value category. Each of these fails silently, so nothing else will catch it.

- **A lint or format CI step that runs the *write* variant.** `prettier --write`,
  `npm run format`, `ruff format` and their like rewrite files and exit 0 whatever the input.
  A step named "Lint" that runs one of these has never failed and never will.
  *Check:* open the workflow, find the step, and read the command it runs. Trace a
  `npm run <script>` to its definition in `package.json`.
  *Fix:* use the check variant — `prettier --check`, `format:check`, `ruff format --check`.
  *Cost of absence:* the project believes formatting is gated. It is not.

- **A deploy triggered by `workflow_run` with no success guard.** A `deploy.yaml` that keys off
  `workflow_run` needs
  `if: github.event.workflow_run.conclusion == 'success'`. Without it, a red build deploys.
  *Check:* read every workflow with a `workflow_run` trigger and look for the guard.
  *Cost of absence:* failing code reaches production. The test suite becomes decorative.

- **CI installing with `npm install` rather than `npm clean-install`.** Or a package manager
  invoked without `--frozen-lockfile`. The lockfile is committed and CI ignores it, so CI resolves
  fresh versions and the build is not reproducible.
  *Check:* read the install step of each workflow.
  *Cost of absence:* CI and local machines run different dependency trees. Failures appear and
  vanish without a code change.

- **A pre-commit hook whose guard is never true.** A hook that tests for a tool the project has
  since replaced never runs its body.
  *Check:* read the hook end to end. Confirm every command it guards on still exists in the
  project.
  *Cost of absence:* the hook appears to protect the repository and does nothing.

- **A script referenced by CI or the README that does not exist.** A workflow step or a README
  instruction naming a script that is absent from `package.json`, `pyproject.toml`, or `bin/`.
  *Check:* list the scripts each manifest defines, then cross-reference every script name the
  workflows and the README mention.
  *Cost of absence:* CI fails confusingly, or the instruction misleads a new contributor.

---

## Category: Security and dependencies

- **Credentials committed to the repository.** Kubeconfigs, `.crt` and `.key` files, `.env`
  files, tokens, and private keys.
  *Check:* search the working tree, then search history — `git log --all --diff-filter=A
  --name-only` and grep the result. A file deleted from the tree is still in history and still
  compromised.
  *Cost of absence:* a live credential leak. This is the one finding that stays urgent after the
  project is dormant.

- **CI actions unpinned.** `actions/checkout@master` or any mutable ref.
  *Check:* read the `uses:` line of every step in every workflow.
  *Cost of absence:* supply-chain exposure. The code that runs in CI can change without a commit
  to this repository.

- **Runtime pinned only inside workflow YAML.** A Node, Python, or Rust version named in the
  workflow but with no `.nvmrc`, `.tool-versions`, `.python-version`, `rust-toolchain.toml`, or
  `engines` field.
  *Check:* find the version the workflow uses, then look for a matching pin file at the root.
  Compare both against current end-of-support dates.
  *Cost of absence:* local machines drift from CI, and nothing prompts an upgrade. This is exactly
  how a project stays on an end-of-life runtime for years.

- **No dependency-update mechanism.** Dependabot, Renovate, or a self-hosted maintenance workflow.
  *Check:* look for `.github/dependabot.yml`, a Renovate config, or a scheduled maintenance
  workflow.
  *Cost of absence:* dependencies age silently until an upgrade becomes a project of its own.
  *Note:* a self-hosted `auto-maintenance.yaml` is the house pattern. It can add a publish
  quarantine. A quarantine holds a new release for a set period before the project adopts it.
  Dependabot cannot do this.

- **Base images and toolchains past end of support.**
  *Check:* read the `FROM` lines in each Dockerfile and the toolchain versions in CI. Compare
  against current support dates.
  *Cost of absence:* security patches stop arriving.

---

## Category: CI/CD

- **A PR-triggered test workflow at all.** The baseline.
  *Check:* confirm at least one workflow has `pull_request` in its `on:` block and runs the tests.
  *Cost of absence:* nothing checks a change before it merges.

- **`concurrency` with `cancel-in-progress`.** Without it, superseded runs keep burning minutes.
  *Cost of absence:* slow feedback and wasted CI time.

- **Explicit `permissions:`.** A workflow with no `permissions:` block inherits the repository
  default, which is usually wider than the job needs.
  *Cost of absence:* a compromised action gets more access than it should have.

- **Separate lint, typecheck and test gates.** One combined step stops at the first failure, so a
  lint error hides every test result behind it.
  *Cost of absence:* one fix per CI round-trip instead of a full failure list.

- **Failure notification.** Something tells somebody when the main branch goes red.
  *Cost of absence:* a red main branch sits unnoticed.

- **Deploy manifests declarative rather than imperative.** A committed manifest, not a
  `kubectl set image` in a workflow step.
  *Cost of absence:* the running state is not in the repository, so nothing can reproduce or
  review it.

- **A multi-stage, self-contained Dockerfile.** Build and runtime stages separated, with no
  dependency on artefacts built outside the image.
  *Cost of absence:* images are larger than they need to be, and a build that works on one
  machine may not work on another.

---

## Category: Tooling

- **Linter and formatter configured, with the config committed.**
  *Check:* find the config file and confirm the manifest declares the tool as a dependency.
  *Tool choices that count:* **ruff** for Python lint and format; **biome**, or eslint with
  prettier, for TypeScript; **clippy** with **rustfmt** for Rust.
  *Cost of absence:* style argument in review instead of design discussion.

- **Typecheck configured, and its strictness asserted locally.** A project that inherits
  strictness from a base config it does not control can lose it in an upgrade.
  *Check:* **pyright** for Python; `strict: true` in `tsconfig.json` for TypeScript. Confirm the
  setting appears in the project's own config, not only in an extended base.
  *Cost of absence:* type coverage weakens without any commit saying so.

- **Lockfile committed.** `uv.lock`, `pnpm-lock.yaml`, `package-lock.json`, `Cargo.lock`.
  *Check:* confirm it is present and not listed in `.gitignore`.
  *Cost of absence:* no reproducible install.

- **`.editorconfig` present, with `root = true`.** Without `root = true` the file does not stop
  the search, so a config further up the filesystem can override it. Check that it covers the
  languages the project uses. Do not judge the settings themselves — a project may reasonably
  differ.
  *Cost of absence:* editors disagree, and diffs fill with whitespace changes.

- **Line endings settled.** Either `.gitattributes` or `.editorconfig` fixes them.
  *Cost of absence:* line endings change per contributor, and whole files show as modified.

- **`.DS_Store` ignored.** On any project with macOS contributors.
  *Cost of absence:* noise commits.

- **Orphaned config.** A config file for a tool the project no longer uses. Nothing removes config
  during a migration, so it accumulates.
  *Check:* for each config file at the root, confirm the tool it configures is still a declared
  dependency and still referenced by a script or workflow. Watch for deprecated filenames of tools
  the project *does* still use.
  *Cost of absence:* a contributor edits a file that has no effect, and the config misdescribes
  the project's stack.

- **Package manager and layout.** Python: **uv** with `pyproject.toml`, the `hatchling` build
  backend, and a `src/` layout. TypeScript: **pnpm** with a workspace file. Rust: **cargo** with
  the edition declared.
  *Cost of absence:* contributors guess at the install command.

- **`bin/` scripts as the command interface.** Gates and common tasks reachable by one obvious
  command rather than a remembered flag string.
  *Cost of absence:* the way to run the gates lives in somebody's shell history.

- **Main branch named `main`, with conventional commit prefixes.** `feat:`, `fix:`, `refactor:`,
  `chore:`.
  *Check:* read the recent history with `git log --oneline`. Look for a stated convention in
  `CLAUDE.md` or a contributing guide.
  *Cost of absence:* the history cannot be scanned or grouped, and no changelog can be generated
  from it.

---

## Category: Dead code

**Expect this gap on almost every project.** Dead code accumulates silently and no other gate
catches it. Recommend a tool unless the project is trivially small.

- **A dead-code tool wired as a CI gate.**
  *Tool choices:* **knip** for TypeScript, with a per-package `knip.json`. **vulture** for Python,
  with `[tool.vulture]` in `pyproject.toml` and a `.vulture_whitelist.py`. Rust gets `dead_code`
  warnings from the compiler, so check that `-D warnings` is set rather than adding a tool;
  **cargo-machete** or **cargo-udeps** covers unused *dependencies*, which is a separate problem.
  *Cost of absence:* the codebase grows code nobody calls, and every reader pays to understand it.

- **The tool is actually run by CI, not merely configured.** The same trap as the write-variant
  lint step: a `knip.json` in the repository proves nothing about whether knip runs.
  *Check:* find the CI step that invokes it. Confirm a finding would fail the job.
  *Cost of absence:* the config is decoration.

- **The whitelist is not burying findings.** A whitelist that only grows is a suppression list.
  It should shrink as the code is cleaned.
  *Check:* read the whitelist's size and its history. Ask whether its entries are still real.
  *Cost of absence:* the gate passes while the problem it was added to catch gets worse.

---

## Category: Architecture fences

**Size-dependent. Recommend with judgement.** A fence is worth it once a codebase has layers a
newcomer could invert. It is overkill on a small one.

**Do not report the absence as a gap on a project too small to have the problem.** Say the project
is below the threshold, give the size that led you there, and move on. Recommending a layering
tool for a two-crate workspace is a false positive.

- **A layering contract enforced as a gate.**
  *Tool choices:* **import-linter** for Python, run as a CI gate. eslint `no-restricted-imports`
  for TypeScript.
  *Cost of absence:* on a codebase large enough to have layers, the layers erode. Each inversion
  looks reasonable alone.

- **Where a fence exists, check it uses the ratchet pattern.** An exception list that may only
  shrink, with the gate failing on a *stale* entry — an exception listed for something already
  fixed. That failure is what forces an exception to be retired in the same commit as the fix.
  *Check:* find the exception list and confirm the gate validates it both ways.
  *Cost of absence:* the exception list grows into a permanent record of defeat. A ratchet is the
  device that stops the drift.

- **A fence that is documented but not enforced is not a fence.** A layering rule in a Markdown
  file, with nothing checking it, is a wish.

---

## Category: Tests and specs

The central question is **"is the test suite mapped to a documented spec?"** — not "are there
tests".

A suite that only its author can evaluate cannot tell you what is covered, what a failure means,
or what was never built. A well-maintained project answers this question. A dormant one rarely
does.

- **Is behaviour written down as a spec?** A `docs/specs/` directory, with each spec point
  carrying a stable id. Without ids there is nothing for a test to reference.
  *Cost of absence:* "what does this system do?" has no answer except the code.

- **Are tests linked back to spec points?** The house device is a `SPEC: area/file#keycode`
  comment in the test, cross-referenced by a checker.
  *Cost of absence:* the spec and the suite drift apart, and neither describes the other.

- **Is the link machine-checked and wired into CI?** A convention nothing enforces decays into a
  convention nothing follows.
  *Check:* find the checker and the CI job that runs it. Prefer a checker that runs as its own
  job, independent of the build — a spec error should still surface when compilation fails.
  *Cost of absence:* the links rot one merge at a time.

- **Does the checker fail on the things that matter?** Orphaned references, duplicate ids, and
  spec points with no test. A checker that only validates syntax is another gate that cannot fail.
  *Cost of absence:* the checker passes while the mapping it exists to protect is broken.

- **Does the project state the limits of its own coverage?** A project that documents "a green
  gate is not coverage", that names what its checker credits on the comment alone, and that lists
  the spec points currently untested, is doing the hygiene. Being explicit about the gap *is* the
  discipline. A suite that silently overstates its reach is worse than a small honest one.

**Where no spec discipline exists at all, this is a recommendation, not a defect.** Retrofitting
spec ids across an existing suite is real work. Report it as a gap, state its cost, and let the
discussion decide. On a project that already carries specs, a *broken* link — an orphaned
reference, or a spec point no test covers — is a straightforward defect.

Also check the ordinary mechanics:

- **Framework configured, with its config committed.** A project relying on bare defaults has
  nowhere to put a setting when it needs one.
  *Tool choices:* **pytest** for Python, configured in `[tool.pytest.ini_options]`, with
  `pythonpath = ["src"]` for a `src/` layout. **vitest** for TypeScript. **Playwright** for
  end-to-end tests.
- **Tests exist in proportion to the codebase.** Count the test files against the source files.
  A handful of suites for a hundred source files is a finding.
- **End-to-end config does not point at a directory that does not exist.** A vitest or Playwright
  config excluding or including an `e2e/` that was never created.
- **Test layers are documented.** What belongs in unit, integration, and end-to-end.

---

## Category: Docs

- **README truthfulness.** The most reliable decay signal, and it tracks attention rather than
  age — an actively-maintained project can still ship the stock framework README from the day it
  was scaffolded.
  *Check:* confirm the README names *this* project, describes the *current* stack, and lists only
  scripts that exist in the manifest. Watch for boilerplate from a framework the project no longer
  uses, or never used.
  *Cost of absence:* the first thing a new contributor reads is wrong.

- **Setup, test and build instructions.** Enough to go from clone to running.
  *Check:* run through them mentally against the manifests. Do not run them.
  *Cost of absence:* onboarding costs a conversation every time.

- **A gates table.** What runs in CI, what each gate checks, and how to run it locally.
  *Cost of absence:* contributors discover the gates by failing them.

- **LICENSE present where the manifest declares one.** A `license` field naming a licence with no
  matching file.
  *Cost of absence:* the licensing is ambiguous.

- **Architecture documentation.** A `docs/` directory covering how the parts fit together.
  *Cost of absence:* every structural question becomes an archaeology exercise.

---

## Category: Maelstrom and agent config

Applies where the project is a maelstrom project, or is intended to be worked on by agents. Skip
the maelstrom-specific checks where `.maelstrom.yaml` is absent and the project is not intended to
have one.

- **`CLAUDE.md` at the root.** How to run the commands, where the docs are, what the architecture
  is.
  *Cost of absence:* every agent session rediscovers the project from scratch.

- **Whether code conventions are documented at all.** `docs/review/coding-standards.md`, and
  `.claude/review-guides/<language>.md` for per-language rules. These are what `/code-review`'s
  sub-agents look for — `reviewer-prompt.md` loads them if they exist.
  *Check:* confirm the files exist and carry real rules.
  *Cost of absence:* every review of this project is weaker, because the reviewer has no project
  standard to judge against and falls back to the surrounding code.
  *Scope:* this check asks whether sufficient conventions **exist**. It does not assess whether
  the code conforms. That is `/code-review`'s job.

- **`.claude/settings.json` permissions.** An allowlist for the commands the project's work
  actually needs.
  *Cost of absence:* a permission prompt on every routine command.

- **`.maelstrom.yaml`.** With `install_cmd`, the port base, and the Linear team where one applies.
  *Cost of absence:* the environment cannot start, and tasks cannot mirror to Linear.

- **`Procfile` with port variables.** For a project that runs services. Ports come from the
  environment, never hardcoded.
  *Cost of absence:* two worktrees of the same project collide on a port.

- **`.env.example` committed, with `.env` ignored.**
  *Check:* confirm `.env` is in `.gitignore` and an example file lists every variable the project
  reads.
  *Cost of absence:* a new worktree has no way to know what configuration it needs — or worse, a
  real `.env` is committed.

- **`.claude/CLAUDE.local.md` gitignored.** It is generated per worktree.
  *Cost of absence:* generated local config lands in the repository and conflicts.
