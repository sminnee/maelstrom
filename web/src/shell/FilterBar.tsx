import { useEffect } from 'react';
import type { AgentStatusFilter, GroupBy } from '../selectors/filters';
import { filterOptions } from '../selectors/filters';
import { useWorld } from '../api/useWorld';
import { TASK_STATUSES, type TaskStatus } from '../protocol/entities';
import { useAppStore } from '../store/store';
import styles from './FilterBar.module.css';

const GROUP_BY_OPTIONS: GroupBy[] = ['project', 'branch', 'worktree', 'none'];
const AGENT_STATUS_OPTIONS: { value: AgentStatusFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'working', label: 'Working' },
  { value: 'idle', label: 'Idle' },
  { value: 'working-idle', label: 'Working + Idle' },
  { value: 'terminated', label: 'Terminated' },
  { value: 'planned', label: 'Planned' },
];

/** The shared filters, plus controls only the current view can use. */
export function FilterBar() {
  const { world } = useWorld();
  const view = useAppStore((s) => s.ui.view);
  const filters = useAppStore((s) => s.ui.filters);
  const listFilters = useAppStore((s) => s.ui.listFilters);
  const groupBy = useAppStore((s) => s.ui.groupBy);
  const setFilters = useAppStore((s) => s.setFilters);
  const setListFilters = useAppStore((s) => s.setListFilters);
  const setGroupBy = useAppStore((s) => s.setGroupBy);
  const options = filterOptions(world, filters);
  const stale = filters.branch !== null && !options.branches.some((b) => b.key === filters.branch);
  useEffect(() => {
    // A branch that left the world would filter to an empty canvas while the
    // select shows "all"; drop it so the control says what the canvas does.
    if (stale) setFilters({ branch: null });
  }, [stale, setFilters]);

  const toggleStatus = (status: TaskStatus) =>
    setListFilters({
      statuses: listFilters.statuses.includes(status)
        ? listFilters.statuses.filter((item) => item !== status)
        : [...listFilters.statuses, status],
    });

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
      {view === 'canvas' ? (
        <>
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
        </>
      ) : (
        <>
          {TASK_STATUSES.map((status) => (
            <label key={status} className={styles.check}>
              <input
                type="checkbox"
                checked={listFilters.statuses.includes(status)}
                onChange={() => toggleStatus(status)}
              />
              <span>{status}</span>
            </label>
          ))}
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
