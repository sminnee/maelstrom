import { describe, expect, it } from 'vitest';
import { filterSeed, noFilters, type Filters } from './filters';

const seed = (filters: Partial<Filters>) => filterSeed({ ...noFilters(), ...filters });

describe('filterSeed', () => {
  it('gives nothing when no filter names a project, branch or comm', () => {
    expect(seed({ text: 'order', agentStatus: 'working' })).toBeNull();
  });

  it('splits a branch key at its first slash, so the branch keeps its own', () => {
    expect(seed({ branch: 'maelstrom/feat/orchestrator-ui' })).toEqual({
      kind: 'filters',
      project: 'maelstrom',
      branch: 'feat/orchestrator-ui',
      comms: [],
    });
  });

  it('takes the project from the Project filter beside a Branch filter', () => {
    expect(seed({ project: 'northwind', branch: 'northwind/fix/db' })).toMatchObject({
      project: 'northwind',
      branch: 'fix/db',
    });
  });

  it('links the comm a Comm filter names, and names no project or branch', () => {
    expect(seed({ comm: 'c2' })).toEqual({
      kind: 'filters',
      project: '',
      branch: '',
      comms: ['c2'],
    });
  });
});
