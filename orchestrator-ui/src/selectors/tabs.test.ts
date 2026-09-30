import { describe, expect, it } from 'vitest';
import {
  changesTab,
  closeTabs,
  documentTab,
  focusedTaskId,
  groupKeyOf,
  groupTabs,
  mostRecentTab,
  openOrFocusTab,
  sessionTab,
  tabAttribution,
  worktreeForTab,
} from './tabs';
import type { WorldView } from './world';
import {
  makeAgent,
  makeDocument,
  makeProject,
  makeTask,
  makeWorktree,
  worldWith,
} from '../test/fixtures';

describe('openOrFocusTab', () => {
  it('adds a new tab and does not add one that is open already', () => {
    const once = openOrFocusTab([], documentTab('doc-1'));
    const twice = openOrFocusTab(once, documentTab('doc-1'));
    expect(twice).toHaveLength(1);
    expect(openOrFocusTab(twice, sessionTab('agent-1'))).toHaveLength(2);
  });
});

describe('closeTabs', () => {
  // Two worktrees: `a1` and `d0` in alpha; `a2`, `d1` and `d2` in bravo.
  const tabs = [
    sessionTab('a1'),
    documentTab('d0'),
    sessionTab('a2'),
    documentTab('d1'),
    documentTab('d2'),
  ];
  const groupOf = (tab: { key: string }) =>
    ['session:a1', 'document:d0'].includes(tab.key) ? 'alpha' : 'bravo';
  const state = (activeTabKey: string, tabRecency: string[]) => ({
    tabs,
    activeTabKey,
    tabRecency,
  });

  it("closing the active tab activates its group's most recent remaining tab", () => {
    const recency = ['document:d0', 'session:a2', 'document:d1', 'session:a1'];
    expect(closeTabs(state('document:d0', recency), ['document:d0'], groupOf)).toEqual({
      tabs: [sessionTab('a1'), sessionTab('a2'), documentTab('d1'), documentTab('d2')],
      activeTabKey: 'session:a1',
      tabRecency: ['session:a2', 'document:d1', 'session:a1'],
    });
  });

  // Two tabs are left in the group, asserted in both recency orders: a rule
  // that reads strip order passes one of them by accident.
  it('prefers recency to strip order, whichever way round they fall', () => {
    for (const [first, second] of [
      ['document:d1', 'document:d2'],
      ['document:d2', 'document:d1'],
    ] as const) {
      const recency = ['session:a2', 'session:a1', first, 'document:d0', second];
      const next = closeTabs(state('session:a2', recency), ['session:a2'], groupOf);
      expect(next.activeTabKey).toBe(first);
    }
  });

  it('falls back to the most recent tab overall when its group is empty', () => {
    const recency = ['document:d1', 'session:a2', 'document:d0', 'session:a1'];
    expect(
      closeTabs(
        state('document:d1', recency),
        ['document:d1', 'session:a2', 'document:d2'],
        groupOf,
      ).activeTabKey,
    ).toBe('document:d0');
  });

  it('closing an inactive tab leaves the active one alone', () => {
    const recency = ['document:d0'];
    expect(closeTabs(state('document:d0', recency), ['document:d1'], groupOf).activeTabKey).toBe(
      'document:d0',
    );
  });

  it('closing the only tab leaves nothing active', () => {
    const one = { tabs: [documentTab('d0')], activeTabKey: 'document:d0', tabRecency: [] };
    expect(closeTabs(one, ['document:d0'], groupOf).activeTabKey).toBeNull();
  });
});

describe('mostRecentTab', () => {
  it('picks the most recently active of the keys, else the first', () => {
    expect(mostRecentTab(['a', 'b', 'c'], ['x', 'c', 'a'])).toBe('c');
    expect(mostRecentTab(['a', 'b'], [])).toBe('a');
    expect(mostRecentTab([], ['a'])).toBeNull();
  });
});

describe('groupTabs', () => {
  const worktrees = [
    makeWorktree({ id: 'northwind-bravo', project: 'northwind', nato: 'bravo' }),
    makeWorktree({ id: '_main', project: 'northwind', nato: '_main' }),
    makeWorktree({ id: 'northwind-alpha', project: 'northwind', nato: 'alpha' }),
    makeWorktree({ id: 'acme-alpha', project: 'acme', nato: 'alpha' }),
  ];
  // A heading reads the project's name, which sorts apart from its id.
  const projects = [makeProject({ id: 'acme', name: 'zenith' }), makeProject()];
  const agents = [
    makeAgent({ id: 'nb', worktreeId: 'northwind-bravo', taskId: 'northwind/NORT-9' }),
    makeAgent({ id: 'nm', worktreeId: '_main', taskId: '' }),
    makeAgent({ id: 'na', worktreeId: 'northwind-alpha', taskId: 'northwind/NORT-7' }),
    makeAgent({ id: 'aa', project: 'acme', worktreeId: 'acme-alpha', taskId: '' }),
    makeAgent({ id: 'nx', worktreeId: 'northwind-gone', taskId: '' }),
  ];
  const tasks = [
    makeTask({ id: 'northwind/NORT-7', notebookId: 'NORT-7' }),
    makeTask({ id: 'northwind/NORT-9', notebookId: 'NORT-9' }),
  ];
  const documents = [
    makeDocument({ id: 'by-agent', agentId: 'nb', taskId: '' }),
    // Its own agent has gone; the agent that runs its task still places it.
    makeDocument({ id: 'by-task', agentId: 'gone', taskId: 'northwind/NORT-7' }),
    // Its own agent is in bravo, and its task runs in alpha: its own agent wins.
    makeDocument({ id: 'own-first', agentId: 'nb', taskId: 'northwind/NORT-7' }),
  ];
  const tabs = [
    sessionTab('nx'),
    sessionTab('nb'),
    documentTab('by-agent'),
    sessionTab('aa'),
    documentTab('by-task'),
    sessionTab('nm'),
    sessionTab('na'),
    documentTab('vanished'),
  ];
  const shape = (world: WorldView) =>
    groupTabs(world, tabs).map((p) => ({
      project: p.label,
      worktrees: p.worktrees.map((g) => [g.label, ...g.tabs.map((t) => t.key)]),
    }));

  // Projects by name, `_main` first, then by nato, however the world arrives.
  it('orders projects by name and worktrees _main first, then by nato, whatever the fixture order', () => {
    const expected = [
      {
        project: 'northwind',
        worktrees: [
          ['_main', 'session:nm'],
          ['alpha', 'document:by-task', 'session:na'],
          ['bravo', 'session:nb', 'document:by-agent'],
          ['no worktree', 'session:nx'],
        ],
      },
      { project: 'zenith', worktrees: [['alpha', 'session:aa']] },
      { project: 'Other', worktrees: [['no worktree', 'document:vanished']] },
    ];
    expect(shape(worldWith({ projects, worktrees, agents, tasks, documents }))).toEqual(expected);
    expect(
      shape(
        worldWith({
          projects: [...projects].reverse(),
          worktrees: [...worktrees].reverse(),
          agents: [...agents].reverse(),
          tasks: [...tasks].reverse(),
          documents: [...documents].reverse(),
        }),
      ),
    ).toEqual(expected);
  });

  // A row must not move when a tab opens: the rows follow names, not tab order.
  it('orders the rows the same whatever order the tabs opened in', () => {
    const world = worldWith({ projects, worktrees, agents, tasks, documents });
    const rows = (open: typeof tabs) =>
      groupTabs(world, open).flatMap((p) => p.worktrees.map((g) => `${p.label} ${g.label}`));
    expect(rows([...tabs].reverse())).toEqual(rows(tabs));
  });

  it('keys a group by its worktree id, and a worktree-less one by its project', () => {
    const world = worldWith({ projects, worktrees, agents, tasks, documents });
    const keys = groupTabs(world, tabs).flatMap((p) => p.worktrees.map((g) => g.key));
    expect(keys).toEqual([
      '_main',
      'northwind-alpha',
      'northwind-bravo',
      'none:northwind',
      'acme-alpha',
      'none:',
    ]);
    expect(groupKeyOf(world, documentTab('by-task'))).toBe('northwind-alpha');
    expect(worktreeForTab(world, documentTab('by-agent'))?.id).toBe('northwind-bravo');
    expect(worktreeForTab(world, documentTab('own-first'))?.id).toBe('northwind-bravo');
    // A changes tab is its worktree's own, with no agent between.
    expect(groupKeyOf(world, changesTab('acme-alpha'))).toBe('acme-alpha');
  });

  // A task relaunched in a new worktree leaves its exited agent behind. The
  // document goes where the work runs now, and never to a subagent.
  it("places an orphaned document with its task's live top-level agent", () => {
    const world = worldWith({
      worktrees,
      agents: [
        makeAgent({ id: 'a-old', worktreeId: 'northwind-alpha', state: 'exited' }),
        makeAgent({ id: 'a-sub', parent: 'z-new', worktreeId: '_main' }),
        makeAgent({ id: 'z-new', worktreeId: 'northwind-bravo' }),
      ],
      documents: [makeDocument({ id: 'doc', agentId: 'gone', taskId: 'NORT-7' })],
    });
    expect(worktreeForTab(world, documentTab('doc'))?.id).toBe('northwind-bravo');
  });
});

const world = worldWith({
  tasks: [makeTask({ id: 'NORT-7', command: 'plan-task' })],
  agents: [makeAgent({ id: 'agent-1', taskId: 'NORT-7' })],
  documents: [makeDocument({ id: 'doc-1', agentId: 'agent-1', taskId: 'NORT-7' })],
});

/** A document whose own task has gone, while its agent still has one. */
const orphaned = worldWith({
  tasks: [makeTask({ id: 'NORT-7', command: 'plan-task' })],
  agents: [makeAgent({ id: 'agent-1', taskId: 'NORT-7' })],
  documents: [makeDocument({ id: 'doc-1', agentId: 'agent-1', taskId: 'gone' })],
});

const freeWorld = worldWith({ agents: [makeAgent({ id: 'd9a4c7f1', taskId: '' })] });

describe('tabAttribution', () => {
  it('names no phase for a tab whose entity has left the world', () => {
    expect(tabAttribution(world, sessionTab('gone'))).toMatchObject({ id: 'gone', phase: null });
  });

  // The id and the phase must name the same task: a chip showing a phase colour
  // beside an empty id says two different things about one tab.
  it("falls back to the agent's task for a document whose own task has gone", () => {
    expect(tabAttribution(orphaned, documentTab('doc-1'))).toMatchObject({
      id: 'NORT-7',
      phase: 'plan',
    });
  });

  // An agent writes a document's title, so an empty one is reachable. A tab
  // with no label reads as a session tab on the same agent.
  it('labels a document whose own title is empty', () => {
    const untitled = worldWith({
      agents: [makeAgent({ id: 'agent-1', taskId: '' })],
      documents: [makeDocument({ id: 'doc-1', agentId: 'agent-1', taskId: '', title: '' })],
    });
    expect(tabAttribution(untitled, documentTab('doc-1'))).toMatchObject({ label: 'Document' });
  });

  // Every tab names itself. A document the world knows nothing about has no
  // task and no agent to fall back to, so its own id is the last resort.
  it('names a document tab whose document has left the world', () => {
    expect(tabAttribution(world, documentTab('vanished'))).toMatchObject({
      id: 'vanished',
      label: 'Document',
    });
  });

  // A session carries no label: the id alone says which session it is, and a
  // real qualified id is long enough that a word beside it squeezes to nothing.
  it("a session tab names its task's qualified id and nothing else", () => {
    expect(tabAttribution(world, sessionTab('agent-1'))).toEqual({
      id: 'NORT-7',
      phase: 'plan',
      agentId: 'agent-1',
      label: '',
      title: 'Add order export',
    });
  });

  // The agent id is the failover task id: the same slot, filled from the next
  // source down, so a free agent's tab is never a bare swatch.
  it('a free agent names its own agent id in the id slot', () => {
    expect(tabAttribution(freeWorld, sessionTab('d9a4c7f1'))).toEqual({
      id: 'd9a4c7f1',
      phase: null,
      agentId: 'd9a4c7f1',
      label: '',
      title: '',
    });
  });

  it("a document tab labels itself with the document's title and carries its agent's task id", () => {
    expect(tabAttribution(world, documentTab('doc-1'))).toEqual({
      id: 'NORT-7',
      phase: 'plan',
      agentId: 'agent-1',
      label: 'Plan',
      title: 'Add order export',
    });
  });
});

describe('focusedTaskId', () => {
  // The canvas's `data-focused` needs a real task id, so it must not read the
  // failover id: a free agent's tab focuses no node.
  it('is null for a free agent whose tab names its agent id', () => {
    expect(focusedTaskId(freeWorld, [sessionTab('d9a4c7f1')], 'session:d9a4c7f1')).toBeNull();
  });

  // The canvas must focus the node the tab names. Both readings resolve the
  // task the same way, so the orphaned document that `tabAttribution` falls
  // back on is the case where they would part company if they diverged.
  it("focuses the agent's task for a document whose own task has gone", () => {
    const tabs = [documentTab('doc-1')];
    expect(focusedTaskId(orphaned, tabs, 'document:doc-1')).toBe('NORT-7');
    expect(tabAttribution(orphaned, tabs[0]!).id).toBe('NORT-7');
  });
});
