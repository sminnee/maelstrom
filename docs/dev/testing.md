# Testing

Each Python contract has one suite that owns it. The other layers test only what they add. When a
rule changes, one suite changes with it, and the suite of the package that holds the rule fails
alone when the rule breaks.

## Which layer owns which contract

| Contract                        | Owning suite                                               |
| ------------------------------- | ---------------------------------------------------------- |
| Store contracts                 | The contract classes in `lib/domain/tests/test_*_store.py` |
| Model rules                     | `lib/domain/tests`                                         |
| Harness model and wire builders | `lib/agent/tests`                                          |
| Daemon reducer                  | `agent-daemon/tests/test_agent_model.py`                   |
| Command refusals                | `orchestrator-api/tests/test_orchestrator_validate.py`     |
| Document tag parsing            | `lib/domain/tests/test_document_tags.py`                   |

An **Outer layer** keeps two tests per command: one success and one handled failure. It also tests
the rules that only it owns:

- argument parsing,
- `-` for stdin,
- the output shape.

Do not repeat a model rule at an outer layer. A copy makes a rule change edit several suites, and it
hides which layer the rule lives in.

## E2E

An e2e test covers one critical path per workflow, on a function-scoped project. It drives the
outermost public entry point of its package: the CLI in `cli/tests/e2e`, the domain functions in
`lib/domain/tests/e2e`.
Put each edge case in the suite of the component that owns it. An e2e test is slow, and a failure in
it names a workflow, not a rule.

## Where test support lives

A test sits in the package of the module it tests. A domain test in `cli/tests` passes in the CLI
suite and leaves `lib/domain/tests` incomplete.

| Module                                | Holds                                         |
| ------------------------------------- | --------------------------------------------- |
| `lib/domain/tests/domain_fixtures.py` | Fixtures that the domain and CLI suites share |
| `lib/domain/tests/git_helpers.py`     | Git verbs for tests: one copy of each         |
| `lib/domain/tests/conftest.py`        | Registers the shared fixtures by name         |
| `cli/tests/conftest.py`               | Registers the same fixtures for the CLI suite |

`pythonpath` in the root `pyproject.toml` puts `lib/domain/tests` and `agent-daemon/tests` on the
import path, so any suite can import the support modules. pytest finds a fixture only when a
conftest imports it by name.

Fakes do not ship in production modules. Two do now: `InMemoryWorktreeSource`
(`orchestrator-api/src/mael_orchestrator/sources.py`) and `ScriptedAsyncDaemonClient`
(`lib/agent/src/mael_agent/agent_transport.py`). Tasks c3t0.2 and c3t0.4 move them.

## Fakes and fixtures

Build a fake's replies with the production builder or mapper. A hand-typed reply stops matching
when the real reply gains a field, and the test still passes.

Record real replies under `fixtures/`. Do not hand-type command output. These suites replay
recordings now:

- `agent-daemon/fixtures/agent_events/`: daemon streams, replayed by the reducer tests.
- `lib/domain/fixtures/normalised/`: the normaliser goldens. Re-record them with
  `UPDATE_GOLDEN=1 uv run pytest lib/domain/tests/test_orchestrator_normalise.py`.
- `agent-daemon/fixtures/agent_state/`: the reducer goldens, one final `AgentState` for each
  stream. Re-record them with
  `UPDATE_GOLDEN=1 uv run pytest agent-daemon/tests/test_agent_model.py`.
- `lib/domain/fixtures/state_db/`: the shape each shipped rung builds, one file for each ladder.
  `UPDATE_GOLDEN=1 uv run pytest lib/domain/tests/test_state_db.py` appends the entries of new
  rungs only. A failing shipped entry is an edit to a shipped rung: add a new rung instead.

## The `e2e` marker

`e2e` is the one marker that selects a suite. The `test` gate runs `uv run pytest -m 'not e2e'`.
CI's `e2e` gate runs `uv run pytest -m e2e`. A test that spawns real processes or uses a real git
repo across a workflow is `e2e`.
