import { useEffect, useMemo, useRef, useState } from 'react';
import type { AgentStatusFilter, Filters } from '../selectors/filters';
import { AGENT_STATUS_LABELS } from '../selectors/filters';
import { filterOptions } from '../selectors/filterOptions';
import { useWorld } from '../api/useWorld';
import { TASK_STATUSES } from '../protocol/entities';
import { MultiSelect } from '../ui/MultiSelect';
import { useShowing } from '../layout/useShowing';
import { useGo, useLoc } from '../nav/useNav';
import type { View } from '../store/uiSlice';
import styles from './FilterBar.module.css';

const TASK_STATUS_OPTIONS = TASK_STATUSES.map((status) => ({ value: status, label: status }));

const AGENT_STATUS_OPTIONS = Object.entries(AGENT_STATUS_LABELS).map(([value, label]) => ({
  value: value as AgentStatusFilter,
  label,
}));

/** The shared filters, plus the controls of each main view on screen. */
export function FilterBar() {
  const { world, status } = useWorld();
  const showing = useShowing();
  const { filters, listFilters, worktreeFilters } = useLoc();
  const go = useGo();
  const setFilters = (patch: Partial<Filters>) => go({ filters: patch });
  const views = useMemo(() => showing.filter((p): p is View => p !== 'tabs'), [showing]);
  const { project, branch } = filters;
  // The canvas options run the graph over every task, so build them only on a change.
  const options = useMemo(
    () => filterOptions(world, { project, branch }, views, worktreeFilters),
    [world, project, branch, views, worktreeFilters],
  );
  // Not while the world loads: a branch from a copied link names work that has not arrived.
  const stale =
    status === 'ready' &&
    filters.branch !== null &&
    !options.branches.some((b) => b.key === filters.branch);
  useEffect(() => {
    // A branch that left the world would filter to an empty canvas while the
    // select shows "all"; drop it so the control says what the canvas does.
    // A replace: the branch was never the user's pick to go back to.
    if (stale) go({ filters: { branch: null } }, { replace: true });
  }, [stale, go]);

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
            onChange={() => go({ worktreeFilters: { showClosed: !worktreeFilters.showClosed } })}
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
        <MultiSelect
          className={styles.field}
          label="Status"
          options={TASK_STATUS_OPTIONS}
          value={listFilters.statuses}
          onChange={(statuses) => go({ listFilters: { statuses } })}
          // The list reads an empty pick as no filter.
          emptyLabel="all"
        />
      )}
      {(showing.includes('canvas') || showing.includes('list')) && <SearchField />}
    </div>
  );
}

/** How long typing pauses before the Search text reaches the URL. */
const SEARCH_COMMIT_MS = 200;

/**
 * The Search field. It holds what is typed and commits it after a pause, so a
 * keystroke does not re-derive every view and refit the canvas.
 */
function SearchField() {
  const committed = useLoc().filters.text;
  const go = useGo();
  const [draft, setDraft] = useState(committed);
  // The text this field last committed, so its own commit coming back from
  // the URL is not read as a change made elsewhere, such as Back.
  const [sent, setSent] = useState(committed);
  const [seen, setSeen] = useState(committed);
  if (seen !== committed) {
    setSeen(committed);
    if (committed !== sent) setDraft(committed);
  }
  const latest = useRef(draft);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // One entry for a search, not one per pause: Back leaves the search, not a word.
  const commit = (text: string) => {
    setSent(text);
    go({ filters: { text } }, { replace: true });
  };
  useEffect(
    () => () => {
      // Closing the Filters side sheet inside the pause must not drop the text.
      if (timer.current === null) return;
      clearTimeout(timer.current);
      go({ filters: { text: latest.current } }, { replace: true });
    },
    [go],
  );
  return (
    <label className={styles.field}>
      <span>Search</span>
      <input
        type="search"
        value={draft}
        onChange={(e) => {
          const text = e.target.value;
          setDraft(text);
          latest.current = text;
          if (timer.current !== null) clearTimeout(timer.current);
          timer.current = setTimeout(() => {
            timer.current = null;
            commit(text);
          }, SEARCH_COMMIT_MS);
        }}
      />
    </label>
  );
}
