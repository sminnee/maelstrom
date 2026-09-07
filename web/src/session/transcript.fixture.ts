import type { TranscriptItem } from '../protocol/transcript';
import type { RequestId, TranscriptItemId } from '../protocol/ids';

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

> A quote, for the rail it draws.

| Token | Value |
| --- | --- |
| \`--text-md\` | 16px |
| \`--measure-prose\` | 80ch |
`;

/** The three waits a decision can be, without an agent to raise them. */
export const questionItem = {
  id: id(),
  ts: at(30),
  type: 'question' as const,
  requestId: 'req-1' as RequestId,
  questions: [
    {
      question: 'Two grouping defaults are plausible. Which should the board open on?',
      header: 'Grouping',
      multiSelect: false,
      options: [
        {
          label: 'By project',
          description: 'One lane per project, which is how the desk is usually read.',
        },
        {
          label: 'By branch',
          description: 'One lane per branch, which suits a single project in depth.',
        },
      ],
    },
  ],
};
