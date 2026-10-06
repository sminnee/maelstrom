# CI gates

Maelstrom's CI runs four gates: `test`, `lint`, `e2e` and `web`. [tangier](https://github.com/sminnee/tangier)
defines them in `pipeline.toml` at the repository root. A gate whose content already has a record
from a dev machine does not run again in CI, so a push after a local run costs CI almost nothing.

## The gates

`pipeline.toml` holds each gate's command and gate scope. `web` is a group of five members:
`web.lint`, `web.typecheck`, `web.knip`, `web.test` and `web.build`. Each member keeps its own
record, so a lint failure does not rerun the build.

A **gate scope** is the set of files a gate reads. tangier hashes the commands and the gate
scope's content into a gate key. A pass is recorded under that key as a git ref under
`refs/tangier/gates/`. Content that already has a record does not run the gate again, whatever
its commit.

## How CI uses them

`.github/workflows/test.yml` has a `gates` job first. It runs
`tangier gate github-outputs` and emits `<gate>-run` for each gate. Each gate job runs only when
its output is not `false`:

- A gate whose gate scope the diff does not touch is `not-needed`. Its job is skipped.
- A gate whose content has a record is `verified`. Its job is skipped.
- Any other gate is `required`. Its job runs `tangier gate run <gate> --read-only`. CI records
  nothing.

The detection fails open. When the `gates` step fails, it sets no output, and every gate job
runs. A job skipped by its `if:` reports success, so the required checks `test`, `lint`, `e2e`
and `web` are always satisfied.

Every gate job runs its gate through `uvx tangier`, so each one needs PyPI to serve tangier. CI
installs the latest release, not a pinned version. When the fetch fails, the jobs fail red. They
do not fall back to the raw command.

## Local passes

`.maelstrom.yaml` sets `pre_push_cmd: tangier gate run test lint web`. `mael gh create-pr` and
`mael sync` run that check before they push, and it records each pass locally. `gate run` then
publishes each record to origin, where CI finds it. `e2e` is too slow for the pre-push check, so it runs in CI only.

Install tangier to run the check:

```bash
uv tool install tangier
tangier gate run test lint web     # run, and record each pass
tangier gate run --all --dry-run   # each gate's status, and what it would run
```

`.github/workflows/gate-sync.yaml` runs `tangier gate sync` once a month. It deletes records older
than `[gate] prune-after-days` in `pipeline.toml`, which is 30.

## Change a gate scope

When a gate command starts to read a new file, add the file to the gate scope in
`pipeline.toml`. A gate scope path is a literal path or `dir/**`. tangier rejects any other glob.

A gate scope that misses an input gives a false pass. A change to that input leaves the key the
same, so CI reuses an old record and never runs the gate on the new content. A change to
`pipeline.toml` or `test.yml` reruns every gate, because `ci-inputs` is in every gate scope.

## SHA buckets

`pipeline.toml` also holds four SHA buckets: `web`, `orchestrator`, `agent-daemon` and `cli`. Its
comments say how each `depends` list is chosen. A SHA bucket named after a service gives that
service its [service version](../guide/dev-environments.md#service-versions). Tests and READMEs
are excluded from the SHA buckets, so a test-only change restarts nothing.

The SHA buckets do not use a global `[sha] exclude`. tangier applies that list to gate scopes
too, so excluding `**/tests/**` there would let a test-only change skip the `test` gate.

The `gates` job runs `tangier changemap sha --all`, so a broken SHA bucket fails CI.
`cli/tests/test_pipeline_config.py` checks that each core service has a SHA bucket.
