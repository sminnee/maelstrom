import { Link } from 'react-router';
import { DESK_MODES, type DeskMode } from '../nav/location';
import { useHrefFor, useLinkState, useLoc } from '../nav/useNav';
import segment from '../ui/SegmentedControl.module.css';
import styles from './DeskModeSwitch.module.css';

const MODE_LABELS: Record<DeskMode, string> = { graph: 'Graph', kanban: 'Kanban', list: 'List' };

/**
 * The **Desk mode** switch at the top of the desk slot. Each mode is a link, so a mode opens
 * in a new window too, as the top bar's view items do.
 */
export function DeskModeSwitch() {
  const { mode } = useLoc();
  return (
    <nav className={styles.bar} aria-label="Desk mode">
      <div className={segment.group}>
        {DESK_MODES.map((m) => (
          <ModeLink key={m} mode={m} current={m === mode} />
        ))}
      </div>
    </nav>
  );
}

function ModeLink({ mode, current }: { mode: DeskMode; current: boolean }) {
  const { card } = useLoc();
  // Only the graph draws a worktree card: the other modes have no box to open it from.
  const to = useHrefFor({
    view: 'desk',
    mode,
    card: mode !== 'graph' && card?.kind === 'worktree' ? null : card,
  });
  const linkState = useLinkState();
  return (
    <Link
      to={to}
      state={linkState}
      className={`${segment.segment} nowrap`}
      aria-current={current ? 'true' : undefined}
    >
      {MODE_LABELS[mode]}
    </Link>
  );
}
