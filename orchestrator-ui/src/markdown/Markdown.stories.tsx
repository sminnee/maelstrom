import type { Story } from '@ladle/react';
import { QueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { ApiProvider } from '../api/ApiProvider';
import { createFakeServer } from '../fake/fakeServer';
import {
  makeAgent,
  makeAttention,
  makeDocument,
  makeTask,
  makeWorktree,
  worldWith,
} from '../fake/fixtures';
import { comm } from '../fake/seedWorld';
import { MemoryNav } from '../nav/MemoryNav';
import docStyles from '../documents/DocumentTab.module.css';
import { Markdown } from './Markdown';
import { planDocument } from './plan.fixture';

export default { title: 'Markdown' };

/**
 * The case the surface is tuned for. Read it at length rather than glancing at
 * it: the rhythm is what this story shows, and a screenshot of the first
 * viewport does not show whether a document holds up over a thousand lines.
 *
 * Every block here should advance a whole number of 24px rows. Two do not, by
 * design — the four-item list, which ends a half row out, and the table. To
 * check:
 *
 *     [...document.querySelector('[class*=markdown]').children].map(el => {
 *       const s = getComputedStyle(el);
 *       const h = el.getBoundingClientRect().height
 *         + parseFloat(s.marginTop) + parseFloat(s.marginBottom);
 *       return [el.tagName, h, h % 24];
 *     })
 */
export const PlanDocument: Story = () => (
  <div style={{ maxWidth: 'var(--measure-prose)', margin: '0 auto', padding: 24 }}>
    <Markdown source={planDocument} />
  </div>
);

const wideTable = `The commands each surface can run today.

| Feature | Action | Today | Backend support |
| --- | --- | --- | --- |
| Files | File existing | Yes | \`DocumentStore.read_file(worktree, path)\` |
| Tasks | Close task | No | \`mael task status done --task-id <id>\` via \`run_cmd_async\` |
| Agents | Resume a stopped agent | Partly | \`AgentDaemon.resume(session_id, spec=AgentSpec(...))\` |
`;

const narrowTable = `| Key | Value |
| --- | --- |
| a | 1 |
`;

/**
 * A surface that does not opt in. The narrow table keeps the column's left
 * edge. The wide table is wider than its 480px column: each column should keep
 * its longest word, and the table should scroll sideways while the paragraph
 * keeps its width.
 */
export const WideTable: Story = () => (
  <div style={{ maxWidth: 480, margin: '0 auto', padding: 24 }}>
    <Markdown source={[narrowTable, wideTable].join('\n')} />
  </div>
);

const widerTable = `A table wider than the container scrolls inside it.

| Column one | Column two | Column three | Column four | Column five | Column six |
| --- | --- | --- | --- | --- | --- |
| \`AgentDaemon.resume(session_id, spec=AgentSpec(...))\` | \`DocumentStore.read_file(worktree, path)\` | \`mael task status done --task-id <id>\` | \`run_cmd_async(cmd, cwd=worktree)\` | \`OrchestratorClient.get_world()\` | \`TaskHomes.resolve(project, task_id)\` |
`;

const nestedTable = `A table in a list item stays in the column.

- An item with a table:

  | Feature | Action | Today | Backend support |
  | --- | --- | --- | --- |
  | Agents | Resume a stopped agent | Partly | \`AgentDaemon.resume(session_id, spec=AgentSpec(...))\` |
`;

/**
 * The Document tab's own \`.body\` and \`.prose\`, 900px wide. The narrow table
 * keeps the column's left edge. The second table grows past the column,
 * centred on it. The third is wider than \`.body\` and scrolls. The table in
 * the list item stays in the column.
 */
export const WideTableBreakout: Story = () => (
  <div
    style={{
      display: 'flex',
      maxWidth: 900,
      margin: '0 auto',
      outline: '1px dashed var(--border)',
    }}
  >
    <div className={docStyles.body}>
      <Markdown
        className={docStyles.prose}
        source={[narrowTable, wideTable, widerTable, nestedTable].join('\n')}
      />
    </div>
  </div>
);

/**
 * The world the link cards read: one target of each kind, in the states a
 * card draws differently.
 */
function linkCardWorld() {
  const prUrl = (n: number) => `https://github.com/o/r/pull/${n}`;
  const world = worldWith({
    tasks: [
      makeTask({ id: 'northwind/NORT-7', notebookId: 'NORT-7', title: 'Add order export' }),
      makeTask({
        id: 'northwind/NORT-8',
        notebookId: 'NORT-8',
        title: 'Retry failed webhooks',
        status: 'in-progress',
        command: 'build',
      }),
      makeTask({
        id: 'northwind/NORT-9',
        notebookId: 'NORT-9',
        title: 'Drop the legacy importer',
        status: 'merged',
      }),
    ],
    agents: [
      makeAgent({ id: 'ag-build', taskId: 'northwind/NORT-8', description: 'Webhook retries' }),
      makeAgent({
        id: 'ag-ask',
        taskId: '',
        description: 'Index reader',
        state: 'awaiting-permission',
        pendingRequestIds: ['req-1'],
      }),
    ],
    attention: [makeAttention({ taskId: 'northwind/NORT-7', agentId: null })],
    worktrees: [
      makeWorktree({ prNumber: 118, prUrl: prUrl(118), prState: 'ready' }),
      makeWorktree({
        id: 'northwind-bravo',
        nato: 'bravo',
        branch: 'fix/webhooks',
        prNumber: 121,
        prUrl: prUrl(121),
        prState: 'ci-failed',
      }),
      makeWorktree({ id: 'northwind-charlie', nato: 'charlie', branch: 'chore/deps' }),
    ],
    documents: [
      makeDocument({ id: 'doc-plan', title: 'Plan', version: 2, status: 'awaiting-review' }),
      makeDocument({ id: 'doc-pr', title: 'PR', version: 1, status: 'approved' }),
    ],
  });
  world.comms['c-open'] = comm({ id: 'c-open', title: 'Release notes', codename: 'brisk-otter' });
  world.comms['c-closed'] = comm({
    id: 'c-closed',
    title: 'Outage note',
    codename: 'calm-heron',
    closedMinutesAgo: 30,
  });
  return world;
}

const linkCards = `Three things wait on you:

- A plan to review: [Plan](mael:doc/doc-plan)
- [A task that needs you](mael:task/northwind/NORT-7)
- [An agent that asks](mael:session/ag-ask)

The rest, for reference:

[Working task](mael:task/northwind/NORT-8)
[Done task](mael:task/northwind/NORT-9)
[Working agent](mael:session/ag-build)
[Approved PR draft](mael:doc/doc-pr?v=1)
[Open comm](mael:comm/brisk-otter)
[Closed comm](mael:comm/c-closed)
[Worktree with a PR](mael:worktree/northwind/alpha)
[Worktree with no PR](mael:worktree/northwind/charlie)
[Ready PR](mael:pr/northwind/118)
[Red PR](mael:pr/northwind/121)
[The old importer task](mael:missing?uri=mael%3Atask%2Fnorthwind)
`;

/** One **Link card** of each target, in each state, and one that cannot be found. */
export const LinkCards: Story = () => {
  const [{ server, queryClient }] = useState(() => ({
    server: createFakeServer({ world: linkCardWorld() }),
    queryClient: new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    }),
  }));
  return (
    <ApiProvider api={server.api} queryClient={queryClient}>
      <MemoryNav>
        <div style={{ maxWidth: 'var(--measure-prose)', margin: '0 auto', padding: 24 }}>
          <Markdown source={linkCards} />
        </div>
      </MemoryNav>
    </ApiProvider>
  );
};
