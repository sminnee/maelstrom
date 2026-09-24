import { describe, expect, it } from 'vitest';
import {
  closeTab,
  documentTab,
  focusedTaskId,
  openOrFocusTab,
  sessionTab,
  tabAttribution,
} from './tabs';
import { makeAgent, makeDocument, makeTask, worldWith } from '../test/fixtures';

describe('openOrFocusTab', () => {
  it('adds a new tab and does not add one that is open already', () => {
    const once = openOrFocusTab([], documentTab('doc-1'));
    const twice = openOrFocusTab(once, documentTab('doc-1'));
    expect(twice).toHaveLength(1);
    expect(openOrFocusTab(twice, sessionTab('agent-1'))).toHaveLength(2);
  });
});

describe('closeTab', () => {
  const tabs = [documentTab('d0'), sessionTab('a1'), documentTab('d1')];

  it('closing the active tab activates its right neighbour', () => {
    expect(closeTab(tabs, 'session:a1', 'session:a1')).toEqual({
      tabs: [documentTab('d0'), documentTab('d1')],
      activeTabKey: 'document:d1',
    });
  });

  it('closing the last active tab activates the one to its left', () => {
    expect(closeTab(tabs, 'document:d1', 'document:d1').activeTabKey).toBe('session:a1');
  });

  it('closing an inactive tab leaves the active one alone', () => {
    expect(closeTab(tabs, 'document:d0', 'document:d1').activeTabKey).toBe('document:d0');
  });

  it('closing the only tab leaves nothing active', () => {
    expect(closeTab([documentTab('d0')], 'document:d0', 'document:d0').activeTabKey).toBeNull();
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
