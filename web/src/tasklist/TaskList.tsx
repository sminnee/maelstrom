import { useMemo } from 'react';
import { filterOptions } from '../selectors/filters';
import { describeState } from '../selectors/status';
import { LIST_STATUSES, listTasks } from '../selectors/taskList';
import { useAppStore } from '../store/store';
import { useCommand } from '../store/useCommand';
import styles from './TaskList.module.css';

/** Every task in the world, and the one place the desk is edited. */
export function TaskList() {
  const world = useAppStore((s) => s.world);
  const filters = useAppStore((s) => s.ui.listFilters);
  const setFilters = useAppStore((s) => s.setListFilters);
  const { send, error } = useCommand();
  const options = filterOptions(world, filters);
  // Re-derived only when the world or the filters move, not on every frame
  // the server publishes.
  const rows = useMemo(() => listTasks(world, filters), [world, filters]);

  const toggleStatus = (status: (typeof LIST_STATUSES)[number]) =>
    setFilters({
      statuses: filters.statuses.includes(status)
        ? filters.statuses.filter((s) => s !== status)
        : [...filters.statuses, status],
    });

  return (
    <div className={styles.view} data-testid="task-list">
      <div className={styles.filters}>
        {LIST_STATUSES.map((status) => (
          <label key={status} className={styles.check}>
            <input
              type="checkbox"
              checked={filters.statuses.includes(status)}
              onChange={() => toggleStatus(status)}
            />
            <span>{status}</span>
          </label>
        ))}
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
          <span>Search</span>
          <input
            type="search"
            value={filters.text}
            onChange={(e) => setFilters({ text: e.target.value })}
          />
        </label>
      </div>
      {error && <p className={styles.error}>{error}</p>}
      <table className={styles.table}>
        <thead>
          <tr>
            <th>desk</th>
            <th>id</th>
            <th>title</th>
            <th>project</th>
            <th>branch</th>
            <th>status</th>
            <th>state</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ task, onDesk, agent }) => (
            <tr key={task.id} data-task-id={task.id} data-on-desk={onDesk}>
              <td>
                <button
                  type="button"
                  onClick={() =>
                    void send({
                      type: onDesk ? 'desk.remove' : 'desk.add',
                      taskId: task.id,
                    })
                  }
                >
                  {onDesk ? 'Remove from desk' : 'Add to desk'}
                </button>
              </td>
              <td className={styles.mono}>{task.id}</td>
              <td>{task.title}</td>
              <td>{task.project}</td>
              <td className={styles.mono}>{task.branch}</td>
              <td>{task.status}</td>
              <td>{describeState(task, agent)}</td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={7} className={styles.empty}>
                No task matches these filters.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
