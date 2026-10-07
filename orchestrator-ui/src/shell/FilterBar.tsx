import { useEffect, useMemo } from 'react';
import type { AgentStatusFilter } from '../selectors/filters';
import { AGENT_STATUS_LABELS } from '../selectors/filters';
import { filterOptions } from '../selectors/filterOptions';
import { useWorld } from '../api/useWorld';
import { TASK_STATUSES } from '../protocol/entities';
import { MultiSelect } from '../ui/MultiSelect';
import { useShowing } from '../layout/useShowing';
import { useAppStore } from '../store/store';
import type { View } from '../store/uiSlice';
import styles from './FilterBar.module.css';

const TASK_STATUS_OPTIONS = TASK_STATUSES.map((status) => ({ value: status, label: status }));

const AGENT_STATUS_OPTIONS = Object.entries(AGENT_STATUS_LABELS).map(([value, label]) => ({
  value: value as AgentStatusFilter,
  label,
}));

/** The shared filters, plus the controls of each main view on screen. */
export function FilterBar() {
  const { world } = useWorld();
  const showing = useShowing();
  const filters = useAppStore((s) => s.ui.filters);
  const listFilters = useAppStore((s) => s.ui.listFilters);
  const worktreeFilters = useAppStore((s) => s.ui.worktreeFilters);
  const setFilters = useAppStore((s) => s.setFilters);
  const setListFilters = useAppStore((s) => s.setListFilters);
  const setWorktreeFilters = useAppStore((s) => s.setWorktreeFilters);
  const views = useMemo(() => showing.filter((p): p is View => p !== 'tabs'), [showing]);
  const { project, branch } = filters;
  // The canvas options run the graph over every task, so build them only on a change.
  const options = useMemo(
    () => filterOptions(world, { project, branch }, views, worktreeFilters),
    [world, project, branch, views, worktreeFilters],
  );
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
      {/* Only for the Desk and Tasks, which name branches through their work: a
          worktree on a branch no work names would vanish from a table that is
          meant to show every one of them. */}
      {(showing.includes('canvas') || showing.includes('list')) && (
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
      )}
      {showing.includes('worktrees') && (
        <label className={styles.check}>
          <input
            type="checkbox"
            checked={worktreeFilters.showClosed}
            onChange={() => setWorktreeFilters({ showClosed: !worktreeFilters.showClosed })}
          />
          <span>show closed</span>
        </label>
      )}
      {showing.includes('canvas') && (
        <label className={styles.field}>
          <span>Agent status</span>
          <select
            value={filters.agentStatus ?? 'all'}
            onChange={(e) => setFilters({ agentStatus: e.target.value as AgentStatusFilter })}
          >
            {AGENT_STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {showing.includes('list') && (
        <>
          <MultiSelect
            className={styles.field}
            label="Status"
            options={TASK_STATUS_OPTIONS}
            value={listFilters.statuses}
            onChange={(statuses) => setListFilters({ statuses })}
            // The list reads an empty pick as no filter.
            emptyLabel="all"
          />
          <label className={styles.field}>
            <span>Search</span>
            <input
              type="search"
              value={listFilters.text}
              onChange={(e) => setListFilters({ text: e.target.value })}
            />
          </label>
        </>
      )}
    </div>
  );
}
