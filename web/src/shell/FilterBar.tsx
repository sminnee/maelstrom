import { useEffect } from 'react';
import type { GroupBy } from '../selectors/filters';
import { filterOptions } from '../selectors/filters';
import { useWorld } from '../api/useWorld';
import { useAppStore } from '../store/store';
import styles from './FilterBar.module.css';

const GROUP_BY_OPTIONS: GroupBy[] = ['project', 'branch', 'none'];

/** Project and branch filters plus the grouping toggle. All client state. */
export function FilterBar() {
  const { world } = useWorld();
  const filters = useAppStore((s) => s.ui.filters);
  const groupBy = useAppStore((s) => s.ui.groupBy);
  const setFilters = useAppStore((s) => s.setFilters);
  const setGroupBy = useAppStore((s) => s.setGroupBy);
  const options = filterOptions(world, filters);
  const stale = filters.branch !== null && !options.branches.some((b) => b.key === filters.branch);
  useEffect(() => {
    // A branch that left the world would filter to an empty canvas while the
    // select shows "all"; drop it so the control says what the canvas does.
    if (stale) setFilters({ branch: null });
  }, [stale, setFilters]);

  return (
    <div className={styles.bar}>
      <label className={styles.field}>
        <span>Project</span>
        <select
          value={filters.project ?? ''}
          onChange={(e) => setFilters({ project: e.target.value || null, branch: null })}
        >
          <option value="">all</option>
          {options.projects.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </label>
      <label className={styles.field}>
        <span>Branch</span>
        <select
          value={filters.branch ?? ''}
          onChange={(e) => setFilters({ branch: e.target.value || null })}
        >
          <option value="">all</option>
          {options.branches.map((b) => (
            <option key={b.key} value={b.key}>
              {b.label}
            </option>
          ))}
        </select>
      </label>
      <label className={styles.field}>
        <span>Group by</span>
        <select value={groupBy} onChange={(e) => setGroupBy(e.target.value as GroupBy)}>
          {GROUP_BY_OPTIONS.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
