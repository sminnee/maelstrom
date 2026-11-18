import { useRef, useState } from 'react';
import { useLayoutMode, type LayoutMode } from '../layout/useLayoutMode';
import type { Operation } from '../protocol/entities';
import { currentStep, operationLine, operationTarget } from '../selectors/operations';
import { useAppStore } from '../store/store';
import { StepMeter } from '../ui/StepMeter';
import { usePresence } from '../ui/usePresence';
import { useOperationList, useStripOperations } from './useConsole';
import styles from './ConsoleStrip.module.css';

/** How many entries each layout has room for before the rest fold into `+N more`. */
const ROOM: Record<LayoutMode, number> = { wide: 3, medium: 2, narrow: 1 };

/**
 * The **Console** strip: one bar at the foot of the body, shown only while an
 * operation runs, one is **Unseen**, or one ended done a moment ago.
 * A click on an entry opens the console on it.
 *
 * `covered` is true while the drawer is in the page: the strip goes at once as
 * the drawer opens, and comes back only once the drawer's exit ends. As the
 * last operation leaves, the strip keeps its entries while it sinks out.
 */
export function ConsoleStrip({ covered = false }: { covered?: boolean }) {
  const live = useStripOperations(useOperationList());
  const mode = useLayoutMode();
  const openConsole = useAppStore((s) => s.openConsole);
  const ref = useRef<HTMLDivElement>(null);
  const { mounted, leaving } = usePresence(!covered && live.length > 0, ref);
  const [last, setLast] = useState(live);
  if (live.length > 0 && live !== last) setLast(live);
  if (covered || !mounted) return null;
  const ops = live.length > 0 ? live : last;
  const shown = ops.slice(0, ROOM[mode]);
  const more = ops.length - shown.length;
  // The frame's one grid row grows from nothing, so the body above shrinks as the strip rises.
  return (
    <div ref={ref} className={styles.frame} data-presence={leaving ? 'out' : 'in'}>
      <section
        className={styles.strip}
        aria-label="Operations now"
        data-testid="console-strip"
        inert={leaving || undefined}
      >
        {shown.map((op) => (
          <Entry key={op.id} op={op} onOpen={() => openConsole(op.id)} />
        ))}
        {more > 0 && (
          <button type="button" className={styles.more} onClick={() => openConsole()}>
            +{more} more
          </button>
        )}
      </section>
    </div>
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
      <span className={`${styles.target} nowrap`}>{operationTarget(op).label}</span>
      {step && <span className={`${styles.step} nowrap`}>{step.name}</span>}
    </button>
  );
}
