import type { TranscriptItem } from '../protocol/transcript';
import type { TranscriptItemId } from '../protocol/ids';

/**
 * A transcript to look at without a server.
 *
 * The panel's hardest problems are visual — how prose ranks against tool calls,
 * how a run of calls reads, where a long document wraps — and jsdom computes no
 * layout, so tests cannot answer them. These items let a story draw the real
 * components at real widths in both colour schemes.
 *
 * Keep them plausible. A fixture of `foo`/`bar` proves the CSS parses; one that
 * reads like a real session is what shows whether the transcript is legible.
 */

let n = 0;
const at = (minute: number) => `2026-09-07T09:${String(minute).padStart(2, '0')}:00.000Z`;
const id = () => `item-${(n += 1)}` as TranscriptItemId;

const say = (markdown: string, minute: number, role: 'user' | 'assistant' = 'assistant') =>
  ({ id: id(), ts: at(minute), type: 'message', role, markdown }) satisfies TranscriptItem;

const ran = (tool: string, input: Record<string, unknown>, minute: number, output?: string) =>
  ({
    id: id(),
    ts: at(minute),
    type: 'tool_call',
    toolUseId: `tu-${n}`,
    tool,
    input,
    status: 'done',
    output,
  }) satisfies TranscriptItem;

/** Prose and tool calls interleaved: the case the panel is built around. */
export const mixedTranscript: TranscriptItem[] = [
  say('Let me check what the userforms model actually carries before I plan the import.', 9),
  ran(
    'Bash',
    { description: 'Check userforms usage in app', command: 'grep -rn EditableFormField' },
    9,
    'app/src/Page.php:12',
  ),
  ran(
    'Read',
    { file_path: 'app/src/SplitFormBlock.php' },
    9,
    'class SplitFormBlock extends BaseElement',
  ),
  ran('Grep', { query: 'ParentClass', description: 'Find the polymorphic workaround' }, 9),
  say(
    '`SplitFormBlock` uses `Fields()` from `ElementForm`. The userforms subtree — `EditableFormField` with its own options, plus `EmailRecipient` — is in scope; `SubmittedForm` records stay out.\n\nA trap worth naming: `EditableFormField` uses a polymorphic `ParentClass`, and this project already carries a bug workaround for it.',
    10,
  ),
  {
    id: id(),
    ts: at(10),
    type: 'shell',
    command: 'git status --short',
    output: ' M app/src/Page.php',
    status: 'done',
  },
  say('Thanks — that confirms the working tree is clean apart from the page.', 11, 'user'),
  ran('Write', { file_path: 'docs/plan.md', content: '# Plan' }, 11),
  ran(
    'Bash',
    { description: 'Run the test suite', command: 'vendor/bin/phpunit' },
    11,
    'OK (42 tests)',
  ),
  say('Tests pass. The importer must set `ParentClass` explicitly for `SplitFormBlock`.', 12),
];

/** A long unbroken run: the case that used to read as ruled paper. */
export const ledgerRun: TranscriptItem[] = [
  say('Sweeping the codebase for every call site.', 20),
  ...['Grep', 'Read', 'Read', 'Bash', 'Read', 'Grep', 'Bash'].map((tool, i) =>
    ran(tool, { description: `step ${i + 1}`, file_path: `src/module/file${i + 1}.ts` }, 20),
  ),
  say('Seven sites, all in one module. That makes this a single change rather than a sweep.', 21),
];

/**
 * Quiet self-talk against the reading rank: the case the two ranks of prose are built for.
 * Long enough to read start to finish, because a one-line message does not
 * show whether 13px muted holds up.
 */
export const quietTranscript: TranscriptItem[] = [
  say('Checking whether the allocator can take another base before I start the service.', 30),
  ran(
    'Bash',
    { description: 'List the bases in use', command: 'mael env list --ports' },
    30,
    'alpha 342  bravo 517',
  ),
  say(
    'Free port 342 before you retry, or the allocator moves every port in this worktree.\n\n<user-attention low>\nThe allocator refuses a base already in use, and 342 is held by `alpha`. I can either free it or let the allocator pick the next one, which would move every service port in this worktree.\n\nI will wait for you rather than renumbering ports underneath a running service.',
    31,
  ),
  say('Free it — nothing is attached to alpha any more.', 31, 'user'),
];

/** A quiet block short enough that the clamp offers no control. */
export const quietShort: TranscriptItem[] = [
  say('<user-attention low>\nOne short line of working detail.', 50),
];

/**
 * A quiet block for each markdown element it might open with — the
 * highest-risk case for the clamp: `max-height` cannot know where a block
 * boundary falls, so a heading, a list, a fence or a table at the top can cut
 * mid-row in a way a plain paragraph does not.
 */
export const quietBlockElements: TranscriptItem[] = [
  say(
    '<user-attention low>\n## A heading first\n\nThen prose long enough to run past the clamp and show whether the fade lands cleanly below a fixed 24px heading.',
    60,
  ),
  say(
    '<user-attention low>\n- First item in a list that opens the block\n- Second item\n- Third item, long enough to wrap onto a second line and test the clamp against list rhythm rather than paragraph rhythm',
    61,
  ),
  say(
    '<user-attention low>\n```ts\nfunction opensWithAFence() {\n  return "chrome alone is about one line";\n}\n```\n\nProse after the fence, to check the clamp still finds it.',
    62,
  ),
  say(
    '<user-attention low>\n| Token | Value |\n| --- | --- |\n| `--text-ui` | 13px |\n| `--fg-recessed` | tracks `--fg-faint` |\n\nProse after the table.',
    63,
  ),
];

/** Every markdown element the agent actually emits, at panel width. */
export const markdownSample = `# A plan document

Prose sits at the reading size, and the measure caps it so a wide panel does not
produce a line the eye cannot track back from.

## What is wrong

1. **Prose and machinery sat at the same rank.** One size step apart, both boxed.
2. **The ramp had four steps for six roles**, so components invented their own.

### src/maelstrom/cli.py

A heading naming a file must keep its case, which is why headings are not
transformed. Inline literals like \`--measure-prose\` sit inside prose.

- A list item, indented proportionately to the body size.
- A second, to show the rhythm between them.

\`\`\`css
.markdown {
  font-size: var(--text-md);
}
\`\`\`

<user-attention low>
Working detail reads at the smaller rank. It holds \`--text-ui\` and a
[link](/docs) like any other prose.

> A quote, for the rail it draws.

| Token | Value |
| --- | --- |
| \`--text-md\` | 16px |
| \`--measure-prose\` | 80ch |
`;
