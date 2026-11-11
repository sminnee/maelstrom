import { useWorld } from '../api/useWorld';
import { useEditor } from '../nav/useOverlays';
import type { Worktree } from '../protocol/entities';
import { worktreeWork } from '../selectors/worktreeTabs';
import { actionIcon } from '../ui/actionIcons';
import { useAnchorName } from '../ui/useAnchorName';
import { usePopoverMenu } from '../ui/usePopoverMenu';
import menuStyles from '../ui/popoverMenu.module.css';
import styles from './TaskLink.module.css';

/**
 * The worktree bar's way to the task dialog. One task shows its id and
 * title; several show `N tasks ▾` and a menu of them; none show nothing. The
 * dialog has Edit and the status control, so the bar does not repeat them.
 */
export function TaskLink({ worktree }: { worktree: Worktree }) {
  const { world } = useWorld();
  const { open } = useEditor();
  const menu = usePopoverMenu();
  const { anchorStyle } = useAnchorName();
  const tasks = worktreeWork(world, worktree).flatMap((w) => w.task ?? []);
  const only = tasks.length === 1 ? tasks[0] : undefined;

  if (only)
    return (
      <button
        type="button"
        className={styles.link}
        title={only.title}
        onClick={() => open(only.id)}
      >
        <span className={styles.id}>{only.notebookId || only.id}</span>
        <span className={`${styles.title} truncate`}>{only.title}</span>
      </button>
    );
  if (tasks.length === 0) return null;
  return (
    <>
      <button
        {...menu.triggerProps}
        type="button"
        className={`${styles.link} ${styles.trigger}`}
        style={anchorStyle}
      >
        {tasks.length} tasks
        {actionIcon('disclose')}
      </button>
      <div {...menu.menuProps} className={`${menuStyles.menu} ${styles.menu}`} style={anchorStyle}>
        {tasks.map((task) => (
          <button
            key={task.id}
            type="button"
            role="menuitem"
            tabIndex={-1}
            className={`${menuStyles.item} ${styles.item}`}
            onClick={() => {
              menu.close();
              open(task.id);
            }}
          >
            <span className={styles.id}>{task.notebookId || task.id}</span>
            <span className="truncate">{task.title}</span>
          </button>
        ))}
      </div>
    </>
  );
}
