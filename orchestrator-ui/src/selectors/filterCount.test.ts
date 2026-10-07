import { describe, expect, it } from 'vitest';
import type { View } from '../store/uiSlice';
import { activeFilterCount } from './filterCount';
import { noFilters, type Filters } from './filters';
import { noListFilters, type ListFilters } from './taskList';
import { noWorktreeFilters, type WorktreeFilters } from './worktrees';

const count = (
  view: View,
  filters: Partial<Filters> = {},
  list: Partial<ListFilters> = {},
  worktrees: Partial<WorktreeFilters> = {},
) =>
  activeFilterCount(
    view,
    { ...noFilters(), ...filters },
    { ...noListFilters(), ...list },
    { ...noWorktreeFilters(), ...worktrees },
  );

describe('activeFilterCount', () => {
  it('counts nothing at the defaults', () => {
    for (const view of ['canvas', 'list', 'worktrees'] as const) expect(count(view)).toBe(0);
  });

  // One row per control: the views that draw it count it, the others do not.
  it.each<[string, Partial<Filters>, Partial<ListFilters>, Partial<WorktreeFilters>, View[]]>([
    ['project', { project: 'northwind' }, {}, {}, ['canvas', 'list', 'worktrees']],
    ['branch', { branch: 'northwind/feat/db' }, {}, {}, ['canvas', 'list']],
    ['agent status', { agentStatus: 'planned' }, {}, {}, ['canvas']],
    ['search', { text: 'order' }, {}, {}, ['canvas', 'list']],
    ['status', {}, { statuses: ['done'] }, {}, ['list']],
    ['show closed', {}, {}, { showClosed: true }, ['worktrees']],
  ])('counts %s only where it is drawn', (_, filters, list, worktrees, counted) => {
    for (const view of ['canvas', 'list', 'worktrees'] as const)
      expect(count(view, filters, list, worktrees)).toBe(counted.includes(view) ? 1 : 0);
  });

  it('counts no search for text that is only spaces', () => {
    expect(count('canvas', { text: '   ' })).toBe(0);
  });

  it('counts the live statuses in any order as the default', () => {
    expect(count('list', {}, { statuses: ['blocked', 'todo', 'in-progress'] })).toBe(0);
  });
});
