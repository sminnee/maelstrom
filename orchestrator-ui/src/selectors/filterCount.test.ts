import { describe, expect, it } from 'vitest';
import type { View } from '../store/uiSlice';
import { activeFilterCount } from './filterCount';
import { noFilters, type Filters } from './filters';
import { noListFilters, type ListFilters } from './taskList';

const count = (view: View, filters: Partial<Filters> = {}, list: Partial<ListFilters> = {}) =>
  activeFilterCount(view, { ...noFilters(), ...filters }, { ...noListFilters(), ...list });

describe('activeFilterCount', () => {
  it('counts nothing at the defaults', () => {
    for (const view of ['desk', 'list', 'comms'] as const) expect(count(view)).toBe(0);
  });

  // One row per control: the views that draw it count it, the others do not.
  it.each<[string, Partial<Filters>, Partial<ListFilters>, View[]]>([
    ['project', { project: 'northwind' }, {}, ['desk', 'list', 'comms']],
    ['branch', { branch: 'northwind/feat/db' }, {}, ['desk', 'list']],
    ['agent status', { agentStatus: 'planned' }, {}, ['desk']],
    ['search', { text: 'order' }, {}, ['desk', 'list']],
    ['status', {}, { statuses: ['done'] }, ['list']],
  ])('counts %s only where it is drawn', (_, filters, list, counted) => {
    for (const view of ['desk', 'list', 'comms'] as const)
      expect(count(view, filters, list)).toBe(counted.includes(view) ? 1 : 0);
  });

  it('counts no search for text that is only spaces', () => {
    expect(count('desk', { text: '   ' })).toBe(0);
  });

  it('counts the live statuses in any order as the default', () => {
    expect(count('list', {}, { statuses: ['blocked', 'todo', 'in-progress'] })).toBe(0);
  });
});
