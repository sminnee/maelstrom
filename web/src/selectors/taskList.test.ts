import { describe, expect, it } from 'vitest';
import { listTasks, noListFilters } from './taskList';
import { makeAgent, makeTask, onDesk, worldWith } from '../test/fixtures';

const tasks = [
  makeTask({ id: 'northwind/NORT-7', notebookId: 'NORT-7', title: 'Add order export' }),
  makeTask({
    id: 'northwind/NORT-9',
    notebookId: 'NORT-9',
    title: 'Migrate the database',
    status: 'done',
    branch: 'feat/db',
  }),
  makeTask({
    id: 'maelstrom/MAEL-1',
    notebookId: 'MAEL-1',
    title: 'Draw the canvas',
    project: 'maelstrom',
    branch: 'feat/canvas',
  }),
];

const world = worldWith({ tasks, desk: onDesk([tasks[0]!]) });

const rows = (over = {}) => listTasks(world, { ...noListFilters(), ...over }).map((r) => r.task.id);

/** Rows with no status narrowing, for the cases that are about another filter. */
const anyStatus = (over = {}) => rows({ statuses: [], ...over });

describe('listTasks', () => {
  it('opens on the live statuses, hiding finished work', () => {
    expect(noListFilters().statuses).toEqual(['todo', 'in-progress', 'blocked']);
    expect(rows()).toEqual(['maelstrom/MAEL-1', 'northwind/NORT-7']);
  });

  it('lists every task, project first then id, when no status is ticked', () => {
    expect(anyStatus()).toEqual(['maelstrom/MAEL-1', 'northwind/NORT-7', 'northwind/NORT-9']);
  });

  it('narrows by status', () => {
    expect(rows({ statuses: ['done'] })).toEqual(['northwind/NORT-9']);
  });

  it('matches text against the id, the notebook id and the title', () => {
    expect(anyStatus({ text: 'NORT-9' })).toEqual(['northwind/NORT-9']);
    expect(anyStatus({ text: 'order export' })).toEqual(['northwind/NORT-7']);
    expect(anyStatus({ text: 'maelstrom/' })).toEqual(['maelstrom/MAEL-1']);
  });

  it('matches text whatever its case', () => {
    expect(anyStatus({ text: 'ORDER' })).toEqual(['northwind/NORT-7']);
  });

  it('narrows by project and by branch', () => {
    expect(anyStatus({ project: 'maelstrom' })).toEqual(['maelstrom/MAEL-1']);
    expect(anyStatus({ branch: 'northwind/feat/db' })).toEqual(['northwind/NORT-9']);
  });

  it('says which rows are on the desk', () => {
    const onIt = Object.fromEntries(
      listTasks(world, { ...noListFilters(), statuses: [] }).map((r) => [r.task.id, r.onDesk]),
    );
    expect(onIt).toEqual({
      'maelstrom/MAEL-1': false,
      'northwind/NORT-7': true,
      'northwind/NORT-9': false,
    });
  });

  it('picks the same agent per row as the canvas does', () => {
    const withAgents = worldWith({
      tasks,
      agents: [
        makeAgent({ id: 'old', taskId: 'northwind/NORT-7', state: 'exited' }),
        makeAgent({ id: 'live', taskId: 'northwind/NORT-7', state: 'processing' }),
        makeAgent({ id: 'gone', taskId: 'northwind/NORT-9', state: 'exited' }),
      ],
    });
    const picked = Object.fromEntries(
      listTasks(withAgents, { ...noListFilters(), statuses: [] }).map((r) => [
        r.task.id,
        r.agent?.id,
      ]),
    );
    expect(picked['northwind/NORT-7']).toBe('live');
    expect(picked['northwind/NORT-9']).toBe('gone');
  });

  it('carries the agent of a task that has one', () => {
    const withAgent = worldWith({
      tasks,
      agents: [makeAgent({ id: 'a1', taskId: 'northwind/NORT-7' })],
    });
    const row = listTasks(withAgent, noListFilters()).find((r) => r.task.id === 'northwind/NORT-7');
    expect(row?.agent?.id).toBe('a1');
  });

  it('gives a free agent no row: the task list lists tasks', () => {
    const world = worldWith({
      tasks: [makeTask({ id: 'T1' })],
      agents: [makeAgent({ id: 'free1', taskId: '' })],
    });
    expect(listTasks(world, noListFilters()).map((r) => r.task.id)).toEqual(['T1']);
  });
});
