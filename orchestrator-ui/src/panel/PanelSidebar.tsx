import type { ProjectGroup, TabGroup } from '../selectors/tabs';
import { useAppStore } from '../store/store';
import { actionIcon } from '../ui/actionIcons';
import { PANEL_GROUP_ID } from './PanelTabs';
import { rowId } from './usePanelGroups';
import styles from './PanelSidebar.module.css';

/**
 * The worktrees with open tabs, under their projects: the upper level of the
 * panel's two tab levels, so a vertical tablist rather than a nav.
 */
export function PanelSidebar({
  groups,
  activeGroup,
  onClose,
}: {
  groups: ProjectGroup[];
  activeGroup: TabGroup | null;
  onClose: (keys: string[]) => void;
}) {
  const selectGroup = useAppStore((s) => s.selectGroup);
  const rows = groups.flatMap((p) => p.worktrees);
  if (rows.length === 0) return null;

  // The rows take one tab stop between them; Up and Down move between rows.
  const onKeyDown = (e: React.KeyboardEvent, key: string) => {
    const index = rows.findIndex((g) => g.key === key);
    const row = rows[index];
    // A key on the close button is the button's own, not the row's.
    if (!row || e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      selectGroup(row.tabs.map((t) => t.key));
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const next = rows[(index + (e.key === 'ArrowDown' ? 1 : rows.length - 1)) % rows.length]!;
      selectGroup(next.tabs.map((t) => t.key));
      e.currentTarget
        .closest('[role="tablist"]')
        ?.querySelector<HTMLElement>(`[data-group-key="${CSS.escape(next.key)}"]`)
        ?.focus();
    }
  };

  return (
    <div
      className={styles.sidebar}
      role="tablist"
      aria-orientation="vertical"
      aria-label="Worktrees"
    >
      {groups.map((project) => (
        <div key={project.project} className={styles.project} role="presentation">
          <div className={styles.heading} role="presentation">
            {project.label}
          </div>
          {project.worktrees.map((group) => {
            const selected = group.key === activeGroup?.key;
            const name = `${project.label} ${group.label}`;
            return (
              <div
                key={group.key}
                id={rowId(group.key)}
                role="tab"
                aria-selected={selected}
                aria-controls={PANEL_GROUP_ID}
                aria-label={name}
                tabIndex={selected ? 0 : -1}
                className={styles.row}
                data-active={selected || undefined}
                data-group-key={group.key}
                title={group.worktree?.branch || undefined}
                onClick={() => selectGroup(group.tabs.map((t) => t.key))}
                onKeyDown={(e) => onKeyDown(e, group.key)}
              >
                <span className={styles.name} data-none={!group.worktree || undefined}>
                  {group.label}
                </span>
                <span className={styles.count}>{group.tabs.length}</span>
                <button
                  type="button"
                  className={styles.close}
                  aria-label={`Close ${name}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onClose(group.tabs.map((t) => t.key));
                  }}
                >
                  {actionIcon('close')}
                </button>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
