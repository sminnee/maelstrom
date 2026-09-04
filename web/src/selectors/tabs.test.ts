import { describe, expect, it } from 'vitest';
import { closeTab, documentTab, openOrFocusTab, sessionTab, tabAttribution } from './tabs';
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

describe('tabAttribution', () => {
  const world = worldWith({
    tasks: [makeTask({ id: 'NORT-7', command: 'plan-task' })],
    agents: [makeAgent({ id: 'agent-1', taskId: 'NORT-7' })],
    documents: [makeDocument({ id: 'doc-1', agentId: 'agent-1', taskId: 'NORT-7' })],
  });

  it('names no phase for a tab whose entity has left the world', () => {
    expect(tabAttribution(world, sessionTab('gone'))).toMatchObject({ taskId: '', phase: null });
  });

  // The id and the phase must name the same task: a chip showing a phase colour
  // beside an empty id says two different things about one tab.
  it("falls back to the agent's task for a document whose own task has gone", () => {
    const orphaned = worldWith({
      tasks: [makeTask({ id: 'NORT-7', command: 'plan-task' })],
      agents: [makeAgent({ id: 'agent-1', taskId: 'NORT-7' })],
      documents: [makeDocument({ id: 'doc-1', agentId: 'agent-1', taskId: 'gone' })],
    });
    expect(tabAttribution(orphaned, documentTab('doc-1'))).toMatchObject({
      taskId: 'NORT-7',
      phase: 'plan',
    });
  });

  it('names the task and phase for each tab kind', () => {
    expect(tabAttribution(world, sessionTab('agent-1'))).toEqual({
      taskId: 'NORT-7',
      phase: 'plan',
      agentId: 'agent-1',
      title: 'session',
    });
    expect(tabAttribution(world, documentTab('doc-1'))).toEqual({
      taskId: 'NORT-7',
      phase: 'plan',
      agentId: 'agent-1',
      title: 'plan.md',
    });
  });
});
