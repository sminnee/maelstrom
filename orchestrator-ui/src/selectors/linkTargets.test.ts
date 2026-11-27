import { describe, expect, it } from 'vitest';

import {
  makeAgent,
  makeAttention,
  makeDocument,
  makeTask,
  makeWorktree,
  worldWith,
} from '../fake/fixtures';
import { comm } from '../fake/seedWorld';
import type { Comm } from '../protocol/entities';
import { resolveLinkTarget, type FoundLink } from './linkTargets';
import { documentTab, sessionTab } from './tabs';
import type { WorldView } from './world';

const PR_URL = 'https://github.com/o/r/pull/118';
const TASK_ID = 'northwind/NORT-7';

const task = makeTask({ id: TASK_ID, notebookId: 'NORT-7', title: 'Add order export' });
const world: WorldView = worldWith({
  tasks: [task, makeTask({ id: 'northwind/NORT-9', prs: [{ number: 90, url: PR_URL }] })],
  agents: [
    makeAgent({ id: 'ag1', taskId: TASK_ID, state: 'idle', description: 'Order export' }),
    makeAgent({
      id: 'ag2',
      taskId: '',
      state: 'awaiting-permission',
      pendingRequestIds: ['req-1'],
    }),
  ],
  worktrees: [makeWorktree({ prNumber: 118, prUrl: PR_URL, prState: 'ready' })],
  documents: [
    makeDocument({ id: 'doc-1', title: 'Plan', version: 2, status: 'awaiting-review' }),
    makeDocument({ id: 'doc-2', title: 'PR', version: 1, status: 'approved' }),
  ],
});
const comms: ReadonlyMap<string, Comm> = new Map([
  ['c1', comm({ id: 'c1', title: 'Release notes', codename: 'brisk-otter', closedMinutesAgo: 5 })],
  ['c2', comm({ id: 'c2', title: 'Launch plan', codename: 'calm-heron' })],
]);

const resolve = (uri: string, inWorld: WorldView = world) => resolveLinkTarget(inWorld, comms, uri);
const foundLink = (uri: string, inWorld?: WorldView): FoundLink => {
  const target = resolve(uri, inWorld);
  if (target.kind === 'missing') throw new Error(`${uri} is missing`);
  return target;
};

describe('resolveLinkTarget: the action of each target', () => {
  it.each([
    ['mael:task/northwind/NORT-7', 'Open', { kind: 'card', card: { kind: 'task', id: TASK_ID } }],
    ['mael:session/ag1', 'Open', { kind: 'tab', tab: sessionTab('ag1') }],
    ['mael:doc/doc-1', 'Review', { kind: 'tab', tab: documentTab('doc-1') }],
    ['mael:doc/doc-2?v=1', 'Open', { kind: 'tab', tab: documentTab('doc-2', { version: 1 }) }],
    ['mael:comm/brisk-otter', 'Open', { kind: 'comm', commId: 'c1' }],
    ['mael:worktree/northwind/alpha', 'Open', { kind: 'worktree', worktreeId: 'northwind-alpha' }],
    ['mael:pr/northwind/118', 'Open on GitHub', { kind: 'external', url: PR_URL }],
  ])('%s reads %s', (uri, label, action) => {
    const target = foundLink(uri);
    expect([target.actionLabel, target.action]).toEqual([label, action]);
  });
});

/** What a card draws, beside its action: the whole reading, so no field goes unchecked. */
const reading = (target: FoundLink) => ({
  title: target.title,
  detail: target.detail,
  words: target.words,
  state: target.state,
});

describe('resolveLinkTarget: the title and the state', () => {
  it('reads a task by its title and id, and its open attention as the state', () => {
    const asked = worldWith({
      tasks: [task],
      attention: [makeAttention({ taskId: TASK_ID, summary: 'Plan awaiting review' })],
    });
    expect(reading(foundLink('mael:task/northwind/NORT-7', asked))).toEqual({
      title: 'Add order export',
      detail: 'NORT-7',
      words: 'Plan awaiting review',
      state: 'needs-attention',
    });
  });

  it('reads a task with nothing open by its progress', () => {
    const merged = worldWith({ tasks: [makeTask({ id: TASK_ID, status: 'merged' })] });
    expect(reading(foundLink('mael:task/northwind/NORT-7', merged))).toMatchObject({
      words: 'Done',
      state: 'done',
    });
  });

  it('says an agent with a pending request waits on you', () => {
    expect(reading(foundLink('mael:session/ag2'))).toEqual({
      title: 'Agent',
      detail: 'ag2',
      words: 'waits on you',
      state: 'needs-attention',
    });
  });

  it('titles an agent with no description by its task', () => {
    const plain = worldWith({
      tasks: [task],
      agents: [makeAgent({ id: 'ag1', taskId: TASK_ID, description: '' })],
    });
    expect(foundLink('mael:session/ag1', plain).title).toBe('Add order export');
  });

  it('titles a document with its version, and an awaiting one asks for attention', () => {
    expect(reading(foundLink('mael:doc/doc-1'))).toEqual({
      title: 'Plan v2',
      detail: '',
      words: 'awaiting review',
      state: 'needs-attention',
    });
  });

  it('opens an earlier version to read, not to review', () => {
    const target = foundLink('mael:doc/doc-1?v=1');
    expect([reading(target), target.actionLabel]).toEqual([
      { title: 'Plan v1', detail: '', words: 'v2 is the latest', state: '' },
      'Open',
    ]);
  });

  it.each([
    ['open', 'mael:comm/c2', { title: 'Launch plan', detail: '', words: 'open', state: '' }],
    [
      'closed',
      'mael:comm/c1',
      { title: 'Release notes', detail: '', words: 'closed', state: 'done' },
    ],
  ])('titles a comm by its own title, and a %s one says so', (_case, uri, expected) => {
    expect(reading(foundLink(uri))).toEqual(expected);
  });

  it('reads a worktree by its name and branch, with its PR and its gate', () => {
    const gate = { jobId: 'j1', state: 'running' as const, gate: 'test', queuePosition: null };
    const gated = worldWith({
      worktrees: [makeWorktree({ prNumber: 118, prUrl: PR_URL, prState: 'ready', gate })],
    });
    const target = foundLink('mael:worktree/northwind/alpha', gated);
    expect([reading(target), target.pr?.number, target.gate]).toEqual([
      { title: 'alpha', detail: 'feat/orders', words: '', state: '' },
      118,
      gate,
    ]);
  });

  it('says a worktree with no PR has none', () => {
    const bare = worldWith({ worktrees: [makeWorktree()] });
    expect(foundLink('mael:worktree/northwind/alpha', bare).words).toBe('no PR');
  });

  it('reads a PR from the worktree that holds it, with its state', () => {
    const target = foundLink('mael:pr/northwind/118');
    expect([reading(target), target.pr?.number]).toEqual([
      { title: 'feat/orders', detail: '', words: 'ready to merge', state: '' },
      118,
    ]);
  });

  it('reads a PR only a task registered, with no state', () => {
    const target = foundLink('mael:pr/northwind/90');
    expect([target.action, target.pr?.state, target.words]).toEqual([
      { kind: 'external', url: PR_URL },
      '',
      '',
    ]);
  });
});

describe('resolveLinkTarget: missing', () => {
  it.each([
    ['a task not in the world', 'mael:task/northwind/NORT-404'],
    ['an agent not in the world', 'mael:session/ag9'],
    ['a document not in the world', 'mael:doc/doc-9'],
    ['a version past the latest', 'mael:doc/doc-1?v=3'],
    ['a comm the comms do not hold', 'mael:comm/c9'],
    ['a worktree not in the world', 'mael:worktree/northwind/zulu'],
    ['a PR of another project', 'mael:pr/acme/118'],
    ['a missing URI', 'mael:missing?uri=mael%3Anonsense'],
    ['a form the client does not know', 'mael:nonsense/x'],
  ])('%s', (_case, uri) => {
    expect(resolve(uri)).toEqual({ kind: 'missing' });
  });
});
