import { describe, expect, it } from 'vitest';
import { filterOptions, noFilters } from './filters';
import { makeProject, makeTask, worldWith } from '../test/fixtures';

describe('filterOptions', () => {
  const world = worldWith({
    projects: [makeProject({ id: 'northwind' }), makeProject({ id: 'maelstrom' })],
    tasks: [
      makeTask({ id: 'T1', project: 'northwind', branch: 'feat/orders' }),
      makeTask({ id: 'T2', project: 'northwind', branch: 'feat/db' }),
      makeTask({ id: 'T3', project: 'maelstrom', branch: 'feat/ui' }),
    ],
  });

  it('lists every project, and every branch keyed by its project so shared names stay apart', () => {
    const shared = worldWith({
      tasks: [
        makeTask({ id: 'T1', project: 'northwind', branch: 'main' }),
        makeTask({ id: 'T2', project: 'maelstrom', branch: 'main' }),
      ],
    });
    expect(filterOptions(shared, noFilters()).branches).toEqual([
      { key: 'maelstrom/main', label: 'maelstrom/main' },
      { key: 'northwind/main', label: 'northwind/main' },
    ]);
    expect(filterOptions(world, noFilters()).projects).toEqual(['maelstrom', 'northwind']);
  });

  it('narrows the branches to the chosen project and drops the project from the label', () => {
    expect(filterOptions(world, { ...noFilters(), project: 'maelstrom' }).branches).toEqual([
      { key: 'maelstrom/feat/ui', label: 'feat/ui' },
    ]);
  });
});
