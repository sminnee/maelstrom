import { describe, expect, it } from 'vitest';
import { filterOptions as optionsFor } from './filterOptions';
import { noFilters, type Filters } from './filters';
import { noWorktreeFilters } from './worktrees';
import type { View } from '../store/uiSlice';
import {
  makeAgent,
  makeDeskEntry,
  makeProject,
  makeTask,
  makeWorktree,
  worldWith,
} from '../fake/fixtures';
import { deskIdForAgent, deskIdForTask } from '../protocol/deskId';
import type { WorldView } from './world';

describe('filterOptions', () => {
  /** The options with closed worktrees hidden, as the Worktrees view opens. */
  const filterOptions = (world: WorldView, filters: Filters, views: View[]) =>
    optionsFor(world, filters, views, noWorktreeFilters());

  const world = worldWith({
    // riverbend has no task, so the list offers no riverbend.
    projects: [
      makeProject({ id: 'northwind' }),
      makeProject({ id: 'maelstrom' }),
      makeProject({ id: 'riverbend' }),
    ],
    tasks: [
      makeTask({ id: 'T1', project: 'northwind', branch: 'feat/orders' }),
      makeTask({ id: 'T2', project: 'northwind', branch: 'feat/db' }),
      makeTask({ id: 'T3', project: 'maelstrom', branch: 'feat/ui' }),
    ],
  });

  /** T1 is on the desk; T3, in another project, is not and has no agent. */
  const desk = worldWith({
    projects: [makeProject({ id: 'northwind' }), makeProject({ id: 'maelstrom' })],
    tasks: [
      makeTask({ id: 'T1', project: 'northwind', branch: 'feat/orders' }),
      makeTask({ id: 'T3', project: 'maelstrom', branch: 'feat/ui' }),
    ],
    desk: [makeDeskEntry({ id: deskIdForTask('T1') })],
  });

  it('lists the projects of every task, and every branch keyed by its project so shared names stay apart', () => {
    const shared = worldWith({
      tasks: [
        makeTask({ id: 'T1', project: 'northwind', branch: 'main' }),
        makeTask({ id: 'T2', project: 'maelstrom', branch: 'main' }),
      ],
    });
    expect(filterOptions(shared, noFilters(), ['list']).branches).toEqual([
      { key: 'maelstrom/main', label: 'maelstrom/main' },
      { key: 'northwind/main', label: 'northwind/main' },
    ]);
    expect(filterOptions(world, noFilters(), ['list']).projects).toEqual([
      'maelstrom',
      'northwind',
    ]);
  });

  it('narrows the branches to the chosen project and drops the project from the label', () => {
    expect(
      filterOptions(world, { ...noFilters(), project: 'maelstrom' }, ['list']).branches,
    ).toEqual([{ key: 'maelstrom/feat/ui', label: 'feat/ui' }]);
  });

  it('offers only the desk work on the canvas', () => {
    expect(filterOptions(desk, noFilters(), ['canvas'])).toEqual({
      projects: ['northwind'],
      branches: [{ key: 'northwind/feat/orders', label: 'northwind/feat/orders' }],
    });
  });

  it("offers a free agent's worktree branch on the canvas", () => {
    const free = worldWith({
      worktrees: [makeWorktree({ id: 'riverbend-alpha', project: 'riverbend', branch: 'fix/map' })],
      agents: [makeAgent({ id: 'free-1', taskId: '', project: '', worktreeId: 'riverbend-alpha' })],
      desk: [makeDeskEntry({ id: deskIdForAgent('free-1') })],
    });
    expect(filterOptions(free, noFilters(), ['canvas'])).toEqual({
      projects: ['riverbend'],
      branches: [{ key: 'riverbend/fix/map', label: 'riverbend/fix/map' }],
    });
  });

  it("offers the project of an open worktree's lane on the canvas, but not its branch", () => {
    const lane = worldWith({
      worktrees: [makeWorktree({ id: 'riverbend-alpha', project: 'riverbend', branch: 'fix/map' })],
    });
    expect(filterOptions(lane, noFilters(), ['canvas'])).toEqual({
      projects: ['riverbend'],
      branches: [],
    });
  });

  it('offers the whole Desk whatever the agent status filter hides', () => {
    // No desk agent is terminated, so this filter draws nothing.
    const filters: Filters = { ...noFilters(), agentStatus: 'terminated' };
    expect(filterOptions(desk, filters, ['canvas'])).toEqual(
      filterOptions(desk, noFilters(), ['canvas']),
    );
    expect(filterOptions(desk, filters, ['canvas']).projects).toEqual(['northwind']);
  });

  it('offers the projects of the worktrees the Worktrees view lists, and no branch', () => {
    const worktrees = worldWith({
      projects: [makeProject({ id: 'northwind' }), makeProject({ id: 'riverbend' })],
      worktrees: [
        makeWorktree({ id: 'maelstrom-alpha', project: 'maelstrom' }),
        makeWorktree({ id: 'tangier-alpha', project: 'tangier', isClosed: true }),
      ],
    });
    expect(filterOptions(worktrees, noFilters(), ['worktrees'])).toEqual({
      projects: ['maelstrom'],
      branches: [],
    });
    // "show closed" lists the closed worktree, so its project is offered too.
    expect(
      optionsFor(worktrees, noFilters(), ['worktrees'], { showClosed: true }).projects,
    ).toEqual(['maelstrom', 'tangier']);
  });

  it('offers the union when the canvas and the list are both on screen', () => {
    expect(filterOptions(desk, noFilters(), ['canvas', 'list'])).toEqual({
      projects: ['maelstrom', 'northwind'],
      branches: [
        { key: 'maelstrom/feat/ui', label: 'maelstrom/feat/ui' },
        { key: 'northwind/feat/orders', label: 'northwind/feat/orders' },
      ],
    });
  });

  it('keeps offering a selected project and branch that the view does not hold', () => {
    // Picked on Tasks, then the user switches to the Desk: the filter stays.
    const filters = { ...noFilters(), project: 'maelstrom', branch: 'maelstrom/feat/ui' };
    expect(filterOptions(desk, filters, ['canvas'])).toEqual({
      projects: ['maelstrom', 'northwind'],
      branches: [{ key: 'maelstrom/feat/ui', label: 'feat/ui' }],
    });
  });

  it('drops a selected branch that nothing in the world names', () => {
    const filters = { ...noFilters(), branch: 'northwind/feat/gone' };
    expect(filterOptions(desk, filters, ['canvas', 'list']).branches).toEqual([
      { key: 'maelstrom/feat/ui', label: 'maelstrom/feat/ui' },
      { key: 'northwind/feat/orders', label: 'northwind/feat/orders' },
    ]);
  });
});
