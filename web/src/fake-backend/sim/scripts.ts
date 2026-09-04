import type { Phase, Task } from '../../protocol/entities';
import type { RawStreamEvent } from '../../protocol/normalise';
import type { Question } from '../../protocol/transcript';
import { EDITS, FILES, ciRun, testRun } from './fakeTools';
import { unifiedDiff } from './diff';
import type { Rng } from './rng';
import { chance, pick } from './rng';

export type Beat =
  | { kind: 'say'; text: string }
  | { kind: 'read'; path: string }
  | { kind: 'edit'; path: string; old: string; new: string }
  | { kind: 'bash'; command: string; output: string; exitCode: number }
  | { kind: 'ask'; questions: Question[] }
  | { kind: 'permission'; tool: string; input: Record<string, unknown>; description: string }
  | { kind: 'plan'; markdown: string }
  | {
      kind: 'finish';
      document?: { kind: 'pr' | 'tasks' | 'review'; title: string; markdown: string };
    }
  | { kind: 'exit'; exitCode: number };

export const say = (text: string): Beat => ({ kind: 'say', text });
export const read = (path: string): Beat => ({ kind: 'read', path });
export const edit = (e: { path: string; old: string; new: string }): Beat => ({
  kind: 'edit',
  ...e,
});
export const bash = (r: { command: string; output: string; exitCode: number }): Beat => ({
  kind: 'bash',
  ...r,
});

/** Two questions at once, the second a multi-select: the shape the prompt must step through. */
export const MULTI_QUESTIONS: Question[] = [
  {
    question: 'Which columns should the export include?',
    header: 'Columns',
    multiSelect: true,
    options: [
      { label: 'Id', description: 'The order id.' },
      { label: 'Customer', description: 'The customer name.' },
      { label: 'Total', description: 'The order total.' },
      { label: 'Status', description: 'Where the order is in fulfilment.' },
    ],
  },
  {
    question: 'Which format should the export use?',
    header: 'Format',
    multiSelect: false,
    options: [
      { label: 'CSV', description: 'Opens anywhere; no types.' },
      { label: 'Parquet', description: 'Typed and compact; needs a reader.' },
    ],
  },
];

/** Each entry is one AskUserQuestion call: one or more questions asked together. */
export const QUESTION_SETS: Question[][] = [
  [
    {
      question: 'Should the export stream rows or build the file first?',
      header: 'Export',
      multiSelect: false,
      options: [
        { label: 'Stream', description: 'Constant memory; no total row count up front.' },
        { label: 'Build first', description: 'Simpler; needs the whole file in memory.' },
      ],
    },
  ],
  [
    {
      question: 'Keep the old collation for existing rows?',
      header: 'Collation',
      multiSelect: false,
      options: [
        { label: 'Keep', description: 'No reindex; mixed ordering until the next dump.' },
        { label: 'Reindex now', description: 'One-off REINDEX; ordering consistent immediately.' },
      ],
    },
  ],
  [
    {
      question: 'Which grouping should the canvas default to?',
      header: 'Grouping',
      multiSelect: false,
      options: [
        { label: 'Project', description: 'One lane per project.' },
        { label: 'Branch', description: 'One lane per branch, labelled with its worktree.' },
      ],
    },
  ],
  MULTI_QUESTIONS,
];

export const PERMISSIONS: Beat[] = [
  {
    kind: 'permission',
    tool: 'Bash',
    input: { command: 'git push --force-with-lease origin HEAD', description: 'Push the branch' },
    description: 'Push the branch to origin',
  },
  {
    kind: 'permission',
    tool: 'Bash',
    input: { command: 'rm -rf .venv && uv sync', description: 'Rebuild the virtualenv' },
    description: 'Rebuild the virtualenv',
  },
  {
    kind: 'permission',
    tool: 'WebFetch',
    input: {
      url: 'https://docs.sqlalchemy.org/en/20/core/engines.html',
      prompt: 'Collation options',
    },
    description: 'Fetch the SQLAlchemy engine docs',
  },
];

export function askBeat(rng: Rng): Beat {
  return { kind: 'ask', questions: pick(rng, QUESTION_SETS) };
}

export function permissionBeat(rng: Rng): Beat {
  return pick(rng, PERMISSIONS);
}

export function planMarkdown(task: Task, revised = false): string {
  const note = revised ? `\n_Revised after review._\n` : '';
  return `# ${task.title}\n${note}\n## Context\n\n${task.content.trim()}\n\n## Change\n\n1. Read the code the task touches and pin the seam.\n2. Write the failing test at that seam.\n3. Make it pass with the smallest change.\n4. Open the PR with the test-shaping decisions in the message.\n\n## Seams under test\n\n- The public entry point the task names.\n- One app-boundary test that the behaviour is visible.\n`;
}

export function prMarkdown(task: Task): string {
  return `# ${task.title}\n\n## Summary\n\nImplements ${task.id} on \`${task.branch}\`.\n\n## Test plan\n\n- [x] Unit tests at the agreed seam\n- [x] \`bin/lint\`\n- [ ] Manual check in a worktree\n\n## Raised by review, not actioned\n\n- Consider a follow-up to batch the writes.\n`;
}

/** The chain a shaping agent proposes. `tasksMarkdown` describes it; approving promotes it. */
export const TASK_CHAIN: { suffix: string; title: string; command: string }[] = [
  { suffix: '1', title: 'Plan the first slice', command: 'plan-task' },
  { suffix: '2', title: 'Build the first slice', command: '' },
  { suffix: '3', title: 'Watch the PR', command: 'watch-pr' },
];

export function tasksMarkdown(task: Task): string {
  let previous = task.id;
  const sections = TASK_CHAIN.map((spec) => {
    const id = `${task.id}.${spec.suffix}`;
    const how = spec.command ? `\`command: ${spec.command}\`` : 'Execute, `mode: auto`';
    const section = `## ${id} — ${spec.title}\n\n${how}, follows ${previous}.`;
    previous = id;
    return section;
  });
  return `# Tasks for ${task.title}\n\n${sections.join('\n\n')}\n`;
}

/** The beats an agent in `phase` plays for `task`. Random choices go through `rng`. */
export function scriptFor(phase: Phase, task: Task, rng: Rng): Beat[] {
  const files = Object.keys(FILES);
  const e1 = pick(rng, EDITS);
  const e2 = pick(rng, EDITS);
  switch (phase) {
    case 'shape':
      return [
        say('Reading the brief and the code it touches.'),
        read(pick(rng, files)),
        read(pick(rng, files)),
        askBeat(rng),
        say('Splitting the work into a chain of tasks.'),
        {
          kind: 'finish',
          document: { kind: 'tasks', title: 'tasks.md', markdown: tasksMarkdown(task) },
        },
      ];
    case 'plan':
      return [
        say('Reading the brief and the code it touches.'),
        read(pick(rng, files)),
        read(pick(rng, files)),
        say('Drafting the plan.'),
        { kind: 'plan', markdown: planMarkdown(task) },
        say('Plan approved. Promoting the tasks and handing over.'),
        { kind: 'finish' },
      ];
    case 'build':
      return [
        say('Starting on the task. Red first.'),
        read(pick(rng, files)),
        edit(e1),
        bash(testRun(false, rng)),
        edit(e2),
        bash(testRun(true, rng)),
        ...(chance(rng, 0.6) ? [permissionBeat(rng)] : []),
        say('Green. Opening the PR.'),
        {
          kind: 'finish',
          document: { kind: 'pr', title: 'PR description', markdown: prMarkdown(task) },
        },
      ];
    case 'land':
      return [
        say('Watching CI on the PR.'),
        bash(ciRun(false, rng)),
        read('.github/workflows/test.yml'),
        edit(pick(rng, EDITS)),
        bash(ciRun(true, rng)),
        say('CI is green. The PR is ready to merge.'),
        { kind: 'finish' },
      ];
  }
}

/** The beats an agent plays after its plan was sent back for changes. */
export function reviseScript(task: Task): Beat[] {
  return [
    say('Revising the plan from the comments.'),
    { kind: 'plan', markdown: planMarkdown(task, true) },
  ];
}

/** The beats an agent plays after the user says something to it. */
export function replyScript(text: string): Beat[] {
  const first = text.split('\n')[0]?.slice(0, 50) ?? '';
  return [say(`Noted: "${first}". Carrying on with that in mind.`)];
}

export interface IdGen {
  next(prefix: string): string;
}

/** The daemon's stream-json events for one beat. `exit` and `finish` effects are the stepper's. */
export function expandBeat(beat: Beat, ids: IdGen, sessionId: string): RawStreamEvent[] {
  const assistant = (content: Record<string, unknown>[]): RawStreamEvent => ({
    type: 'assistant',
    message: { role: 'assistant', content },
    session_id: sessionId,
  });
  const toolUse = (name: string, input: Record<string, unknown>) => {
    const id = ids.next('toolu');
    return { id, block: { type: 'tool_use', id, name, input } };
  };
  const toolResult = (toolUseId: string, content: string, isError = false): RawStreamEvent => ({
    type: 'user',
    message: {
      role: 'user',
      content: [{ type: 'tool_result', tool_use_id: toolUseId, content, is_error: isError }],
    },
    session_id: sessionId,
  });
  const controlRequest = (
    toolUseId: string,
    tool: string,
    input: Record<string, unknown>,
    description = '',
  ): RawStreamEvent => ({
    type: 'control_request',
    request_id: ids.next('req'),
    request: {
      subtype: 'can_use_tool',
      tool_name: tool,
      input,
      tool_use_id: toolUseId,
      description,
    },
  });

  switch (beat.kind) {
    case 'say':
      return [assistant([{ type: 'text', text: beat.text }])];
    case 'read': {
      const t = toolUse('Read', { file_path: beat.path });
      return [assistant([t.block]), toolResult(t.id, FILES[beat.path] ?? '')];
    }
    case 'edit': {
      const t = toolUse('Edit', {
        file_path: beat.path,
        old_string: beat.old,
        new_string: beat.new,
      });
      return [assistant([t.block]), toolResult(t.id, unifiedDiff(beat.path, beat.old, beat.new))];
    }
    case 'bash': {
      const t = toolUse('Bash', { command: beat.command });
      return [assistant([t.block]), toolResult(t.id, beat.output, beat.exitCode !== 0)];
    }
    case 'ask': {
      const input = { questions: beat.questions };
      const t = toolUse('AskUserQuestion', input);
      return [assistant([t.block]), controlRequest(t.id, 'AskUserQuestion', input)];
    }
    case 'permission': {
      const t = toolUse(beat.tool, beat.input);
      return [assistant([t.block]), controlRequest(t.id, beat.tool, beat.input, beat.description)];
    }
    case 'plan': {
      const input = {
        plan: beat.markdown,
        planFilePath: `/Users/dev/.claude/plans/${ids.next('plan')}.md`,
      };
      const t = toolUse('ExitPlanMode', input);
      return [
        assistant([{ type: 'text', text: 'The plan is ready for review.' }]),
        assistant([t.block]),
        controlRequest(t.id, 'ExitPlanMode', input),
      ];
    }
    case 'finish':
      return [
        assistant([{ type: 'text', text: 'Done. Handing the task back.' }]),
        {
          type: 'result',
          subtype: 'success',
          total_cost_usd: 0,
          duration_ms: 0,
          session_id: sessionId,
        },
      ];
    case 'exit':
      return [];
  }
}
