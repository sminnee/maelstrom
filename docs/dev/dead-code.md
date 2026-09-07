# Dead code

Nothing calls dead code, so nothing tells you it is there. Two gates find it: vulture reads the
Python, knip reads the TypeScript. Run each one through its script, `bin/vulture-check` and
`bin/knip-check`, never through the tool directly. Each script runs the tool twice, and one run
alone answers the wrong question.

## Two passes

"Unused" means two different things, and no single pass proves both. Leaving `tests/` out of the
graph is what stops test-only use from hiding dead production code. It is also what makes an
unused test helper invisible.

| Pass       | Reads            | A finding means          | Result          |
| ---------- | ---------------- | ------------------------ | --------------- |
| Test       | `src/`, `tests/` | Nothing at all uses this | Fails the build |
| Production | `src/` only      | Only tests reach this    | Prints only     |

**Only the test pass fails the build.** A symbol neither `src/` nor `tests/` uses is dead by any
reading. A symbol only tests reach is different: an in-memory store or a helper a test drives is a
seam by design, and the pass cannot tell a seam from production code that lost its last caller.
A human reads that list instead.

Read the production list when you change a module. An entry you expected production code to call is
a real finding. It is more often a missing feature than dead code — `useResume` sits in the
production list because no part of the app resumes an agent yet.

## What to do with a finding

Ask which pass reported it.

| Reported by         | Do this                                                        |
| ------------------- | -------------------------------------------------------------- |
| The test pass       | Delete it. Neither `src/` nor `tests/` uses it.                  |
| The production pass | Decide. A test seam stays. A lost caller is a bug or dead code.  |

The test pass reports a strict subset, so a symbol it names the production pass names too. Only a
symbol that lives in `tests/` reaches the first row alone, because the production pass never reads
that directory.

**Check the finding against its framework before you delete it.** A framework holds the only
reference to the code it calls, so framework code and dead code look the same. `list_commands` in
`admin_cli.py` is a `click.Group` override. Click calls it by name when it renders help. Deleting
it breaks `mael self-env --help`, and no test fails.

## How to silence a false positive

Take the first option that fits. Each one down the list is broader than the last.

1. **Delete the code.** Most findings are correct.
2. **Un-export it**, for TypeScript. An export used only inside its own file needs no `export`.
3. **A decorator pattern** in `ignore_decorators`, when a decorator marks the whole category. This
   covers every Click command through `@*.command` and `@*.group`.
4. **A name pattern** in `ignore_names`, when the category has no decorator. Textual resolves
   `compose` and `on_*` by name, and `InMemory*` names the storage layer's test seam.
5. **A whitelist entry** in `.vulture_whitelist.py`, for a field or attribute no pattern reaches.

**Every whitelist entry needs a comment saying why the code is not dead.** An entry with no reason
cannot be told apart from real dead code, and the next reader cannot tell whether removing it is
safe.

Vulture matches a whitelist name anywhere, not in one file. Before you add a name, check that no
other module has a finding of the same name. A generic name such as `id` or `status` hides every
finding of that name in the codebase, so keep the whitelist to names that earn their place.

**Audit the whitelist by deleting from it.** Remove an entry and run the gate. A gate that stays
green means the entry was doing nothing, and a dead entry is worse than none: it hides any future
finding of that name. The whitelist went from 98 entries to 43 this way.

## Python

`pyproject.toml` holds `[tool.vulture]`, which configures the production pass. Vulture reads one
such table and has no profiles, so `bin/vulture-check` spells the test pass out as command-line
flags. Those flags replace the table rather than adding to it, so the test pass restates every
setting.

That duplication fails silently, and nothing checks it. Change `[tool.vulture]` and change
`bin/vulture-check` in the same edit. A setting added to one alone changes what the test pass
reports, and no gate says so.

The whitelist carries the wire fields of `orchestrator/protocol.py`. Python writes each field and
TypeScript reads it, so no Python call site exists. Regenerate the entries with:

```bash
uv run vulture src/maelstrom/orchestrator/protocol.py --make-whitelist
```

Excluding that file would be shorter. It would also stop the gate checking the functions in it, so
the whitelist carries the fields instead. One of those functions, `state_with`, is in the
production list today.

`bin/lint` runs `bin/vulture-check`, so a commit and a release both pass through it —
`bin/publish` runs `bin/lint` before it uploads.

## TypeScript

`web/knip.json` configures the test pass. `web/knip.production.json` configures the production pass
and differs by four lines: it ignores `src/test/**` and `src/session/transcript.fixture.ts`. Knip 6
has no `extends`, so the two are whole files rather than one and a delta.

`ignoreExportsUsedInFile` is what makes the gate usable. It drops the two false positives that
dominate: a constant used only in the file that exports it, and a member of a union that callers
import through the union. Without it the `TranscriptItem` members report as dead, and the build
needs them.

Knip finds the vite, vitest and eslint entry points by itself. The config names only
`src/**/*.stories.tsx`, because Ladle reaches a story and nothing else does.

`bin/knip-check` covers `web/` alone. `tools/mael-session-channel` has no tsconfig and no CI job, so
covering it means building a gate for it first.

`bin/publish` does not run knip. It ships the Python wheel, and the web app is not in it, so the
release path covers vulture only.

## What CI runs

The `lint` job runs `bin/lint`, which includes vulture. The `web` job runs `bin/knip-check`.

`.github/workflows/test.yml` decides which jobs run from the changed paths. That filter has one gap
worth knowing. A change under `src/` runs the Python gates, and a change under `web/` runs the web
gates, so a change that strands code in the other language can pass. The filter names
`orchestrator/protocol.py` on the web side for this reason: deleting a wire field there runs knip
over the TypeScript that reads it. Other cross-language edits stay uncovered.
