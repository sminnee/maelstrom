import type { View } from '../store/uiSlice';
import type { Filters } from './filters';
import { searching } from './filters';
import type { ListFilters } from './taskList';
import { LIVE_STATUSES } from './taskList';

/**
 * How many of `view`'s filters are off their default: the narrow Filters
 * button's count. Which view has which control mirrors `shell/FilterBar.tsx`;
 * a control added there is counted here too.
 */
export function activeFilterCount(view: View, filters: Filters, listFilters: ListFilters): number {
  const work = view === 'desk' || view === 'list';
  return [
    filters.project !== null,
    work && filters.branch !== null,
    view === 'desk' && (filters.agentStatus ?? 'all') !== 'all',
    work && searching(filters),
    work && !!filters.comm,
    view === 'list' && !sameStatuses(listFilters.statuses, LIVE_STATUSES),
  ].filter(Boolean).length;
}

const sameStatuses = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((s) => b.includes(s));
