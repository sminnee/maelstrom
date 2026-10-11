import { useLayoutMode, type LayoutMode } from '../layout/useLayoutMode';
import type { Operation } from '../protocol/entities';
import { currentStep, operationLine } from '../selectors/operations';
import { useAppStore } from '../store/store';
import { StepMeter } from '../ui/StepMeter';
import { useOperationList, useStripOperations } from './useConsole';
import styles from './ConsoleStrip.module.css';

/** How many entries each layout has room for before the rest fold into `+N more`. */
const ROOM: Record<LayoutMode, number> = { wide: 3, medium: 2, narrow: 1 };

/**
 * The **Console** strip: one bar at the foot of the body, shown only while an
 * operation runs, one is **Unseen**, or one ended done a moment ago.
 * A click on an entry opens the console on it.
 */
export function ConsoleStrip() {
  const ops = useStripOperations(useOperationList());
  const mode = useLayoutMode();
  const open = useAppStore((s) => s.ui.console.open);
  const openConsole = useAppStore((s) => s.openConsole);
  if (open || ops.length === 0) return null;
  const shown = ops.slice(0, ROOM[mode]);
  const more = ops.length - shown.length;
  return (
    <section className={styles.strip} aria-label="Operations now" data-testid="console-strip">
      {shown.map((op) => (
        <Entry key={op.id} op={op} onOpen={() => openConsole(op.id)} />
      ))}
      {more > 0 && (
        <button type="button" className={styles.more} onClick={() => openConsole()}>
          +{more} more
        </button>
      )}
    </section>
  );
}

function Entry({ op, onOpen }: { op: Operation; onOpen: () => void }) {
  const step = currentStep(op);
  return (
    <button
      type="button"
      className={styles.entry}
      data-operation-id={op.id}
      data-state={op.state}
      onClick={onOpen}
    >
      <StepMeter op={op} />
      <span className={`${styles.words} truncate`}>{operationLine(op)}</span>
      <span className={`${styles.target} nowrap`}>{op.worktreeId}</span>
      {step && <span className={`${styles.step} nowrap`}>{step.name}</span>}
    </button>
  );
}
