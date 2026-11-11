import { describe, expect, it } from 'vitest';
import { deskIdForAgent, deskIdForTask } from '../protocol/deskId';
import { makeAgent, makeDocument, makeTask, makeWorktree, worldWith } from '../fake/fixtures';
import { worktreeTabOffers, worktreeWork } from './worktreeTabs';

const alpha = makeWorktree();
const bravo = makeWorktree({ id: 'northwind-bravo', nato: 'bravo', branch: 'feat/db' });
const NOW = Date.parse('2026-11-11T12:00:00Z');
const onDesk = (...ids: string[]) =>
  ids.map((id) => ({
    id: id.startsWith('agent:') ? id : deskIdForTask(id),
    addedAt: '2026-11-11T08:00:00Z',
  }));

describe('worktreeWork', () => {
  it('lists the desk entries whose card opens a tab here, and the desk Holders', () => {
    const world = worldWith({
      worktrees: [alpha, bravo],
      tasks: [
        // On alpha's branch, never run: a Holder with no tab yet.
        makeTask({ id: 'NORT-7', notebookId: 'NORT-7' }),
        // Done, on another branch, but its old run was here.
        makeTask({ id: 'NORT-3', notebookId: 'NORT-3', status: 'done', branch: 'fix/old' }),
        // Its old run was here, but it is off the desk.
        makeTask({ id: 'NORT-2', notebookId: 'NORT-2', status: 'done', branch: 'fix/older' }),
        // Ran here once, and runs in bravo now.
        makeTask({ id: 'NORT-9', notebookId: 'NORT-9', status: 'done', branch: 'feat/db' }),
        // On the desk, and it only ever ran in bravo.
        makeTask({ id: 'NORT-12', notebookId: 'NORT-12', branch: 'feat/db' }),
      ],
      agents: [
        makeAgent({ id: 'old', taskId: 'NORT-3', state: 'exited' }),
        makeAgent({ id: 'older', taskId: 'NORT-2', state: 'exited' }),
        makeAgent({ id: 'was-here', taskId: 'NORT-9', state: 'exited' }),
        makeAgent({ id: 'there', taskId: 'NORT-9', worktreeId: 'northwind-bravo' }),
        makeAgent({ id: 'elsewhere', taskId: 'NORT-12', worktreeId: 'northwind-bravo' }),
        makeAgent({ id: 'free', taskId: '' }),
        makeAgent({ id: 'free.1', taskId: '', parent: 'free' }),
      ],
      desk: onDesk('NORT-7', 'NORT-3', 'NORT-9', 'NORT-12', deskIdForAgent('free')),
    });
    expect(worktreeWork(world, alpha).map((w) => [w.owner, w.sessions.map((a) => a.id)])).toEqual([
      ['free', ['free']],
      ['NORT-3', ['old']],
      ['NORT-7', []],
      // Only the session that opens here: bravo's is bravo's.
      ['NORT-9', ['was-here']],
    ]);
  });
});

describe('worktreeTabOffers', () => {
  it('lists sessions newest first, with no prefix for one task', () => {
    const world = worldWith({
      worktrees: [alpha],
      tasks: [makeTask()],
      agents: [
        makeAgent({
          id: 'first',
          state: 'exited',
          exitCode: 0,
          startedAt: '2026-11-11T09:00:00Z',
        }),
        makeAgent({ id: 'second', session: 'abcdef1234', startedAt: '2026-11-11T10:00:00Z' }),
        makeAgent({ id: 'second.1', parent: 'second', startedAt: '2026-11-11T11:00:00Z' }),
      ],
      desk: onDesk('NORT-7'),
    });
    const { sessions } = worktreeTabOffers(world, alpha, NOW);
    expect(sessions.map((s) => [s.tab.key, s.prefix])).toEqual([
      ['session:second', null],
      ['session:first', null],
    ]);
    // The start time is in the reader's locale, so only the tail is literal.
    expect(sessions[0]!.label).toMatch(/ · Live · abcdef12$/);
    expect(sessions[1]!.label).toMatch(/ · Finished · sess-1$/);
  });

  it('lists owned documents, then linked ones that open here, once each', () => {
    const world = worldWith({
      worktrees: [alpha, bravo],
      tasks: [
        makeTask(),
        // On alpha's branch too: a second Holder, so the rows carry task ids.
        makeTask({ id: 'NORT-9', notebookId: 'NORT-9' }),
        makeTask({ id: 'NORT-12', notebookId: 'NORT-12', branch: 'feat/db' }),
      ],
      agents: [makeAgent(), makeAgent({ id: 'there', taskId: 'NORT-12', worktreeId: bravo.id })],
      documents: [
        makeDocument({ id: 'plan', title: 'Plan', version: 2, status: 'approved' }),
        makeDocument({ id: 'old', title: 'Old', archived: true }),
        // NORT-9 owns it and NORT-7 links it: it reads as NORT-9's own.
        makeDocument({
          id: 'notes',
          title: 'Notes',
          taskId: 'NORT-9',
          links: [{ taskId: 'NORT-7', fragment: '' }],
        }),
        // NORT-7 links a heading of a document its own agent wrote for NORT-12.
        makeDocument({
          id: 'spec',
          title: 'Spec',
          taskId: 'NORT-12',
          links: [{ taskId: 'NORT-7', fragment: 'api' }],
        }),
        // NORT-7 links it, but it opens in bravo, where its agent runs.
        makeDocument({
          id: 'db-plan',
          taskId: 'NORT-12',
          agentId: 'there',
          links: [{ taskId: 'NORT-7', fragment: '' }],
        }),
      ],
      // NORT-12 is off the desk: its own card is not drawn.
      desk: onDesk('NORT-7', 'NORT-9'),
    });
    const { documents } = worktreeTabOffers(world, alpha, NOW);
    expect(documents.map((d) => [d.tab.key, d.label, d.prefix])).toEqual([
      ['document:plan', 'Plan v2 · approved', 'NORT-7'],
      ['document:notes', 'Notes v1 · awaiting review', 'NORT-9'],
      ['document:spec', 'Spec #api v1 · awaiting review', 'NORT-7'],
    ]);
  });

  it('prefixes each row with its owner when a task and a free agent share the worktree', () => {
    const world = worldWith({
      worktrees: [alpha],
      tasks: [makeTask()],
      agents: [
        makeAgent({ startedAt: '2026-11-11T10:00:00Z' }),
        makeAgent({ id: 'free', taskId: '', startedAt: '2026-11-11T09:00:00Z' }),
      ],
      documents: [makeDocument({ id: 'notes', taskId: '', agentId: 'free' })],
      desk: onDesk('NORT-7', deskIdForAgent('free')),
    });
    const { sessions, documents } = worktreeTabOffers(world, alpha, NOW);
    expect(sessions.map((s) => [s.tab.key, s.prefix])).toEqual([
      ['session:agent-1', 'NORT-7'],
      ['session:free', 'free'],
    ]);
    expect(documents.map((d) => [d.tab.key, d.prefix])).toEqual([['document:notes', 'free']]);
  });

  it('offers changes for an open worktree and none for a closed one', () => {
    const closed = makeWorktree({ isClosed: true });
    const world = worldWith({ worktrees: [alpha] });
    expect(worktreeTabOffers(world, alpha, NOW).changes?.key).toBe('changes:northwind-alpha');
    expect(worktreeTabOffers(worldWith({ worktrees: [closed] }), closed, NOW).changes).toBeNull();
  });
});
