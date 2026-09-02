import { describe, expect, it } from 'vitest';
import {
  closeTab,
  documentTab,
  openOrFocusTab,
  sessionTab,
  summaryTab,
  tabAttribution,
} from './tabs';
import { makeAgent, makeDocument, makeTask, worldWith } from '../test/fixtures';

describe('openOrFocusTab', () => {
  it('adds a new tab and does not add one that is open already', () => {
    const once = openOrFocusTab([], summaryTab('NORT-7'));
    const twice = openOrFocusTab(once, summaryTab('NORT-7'));
    expect(twice).toHaveLength(1);
    expect(openOrFocusTab(twice, sessionTab('agent-1'))).toHaveLength(2);
  });
});

describe('closeTab', () => {
  const tabs = [summaryTab('A'), sessionTab('a1'), documentTab('d1')];

  it('closing the active tab activates its right neighbour', () => {
    expect(closeTab(tabs, 'session:a1', 'session:a1')).toEqual({
      tabs: [summaryTab('A'), documentTab('d1')],
      activeTabKey: 'document:d1',
    });
  });

  it('closing the last active tab activates the one to its left', () => {
    expect(closeTab(tabs, 'document:d1', 'document:d1').activeTabKey).toBe('session:a1');
  });

  it('closing an inactive tab leaves the active one alone', () => {
    expect(closeTab(tabs, 'summary:A', 'document:d1').activeTabKey).toBe('summary:A');
  });

  it('closing the only tab leaves nothing active', () => {
    expect(closeTab([summaryTab('A')], 'summary:A', 'summary:A').activeTabKey).toBeNull();
  });
});

describe('tabAttribution', () => {
  const world = worldWith({
    tasks: [makeTask({ id: 'NORT-7', phase: 'planning' })],
    agents: [makeAgent({ id: 'agent-1', taskId: 'NORT-7', phase: 'planning' })],
    documents: [makeDocument({ id: 'doc-1', agentId: 'agent-1', taskId: 'NORT-7' })],
  });

  it('names no phase for a tab whose entity has left the world', () => {
    expect(tabAttribution(world, sessionTab('gone'))).toMatchObject({ taskId: '', phase: null });
  });

  it('names the task and phase for each tab kind', () => {
    expect(tabAttribution(world, summaryTab('NORT-7'))).toEqual({
      taskId: 'NORT-7',
      phase: 'planning',
      agentId: 'agent-1',
      title: 'summary',
    });
    expect(tabAttribution(world, sessionTab('agent-1'))).toMatchObject({
      taskId: 'NORT-7',
      phase: 'planning',
      title: 'session',
    });
    expect(tabAttribution(world, documentTab('doc-1'))).toMatchObject({
      taskId: 'NORT-7',
      phase: 'planning',
      title: 'plan.md',
    });
  });
});
