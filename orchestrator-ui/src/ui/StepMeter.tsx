import type { Operation } from '../protocol/entities';
import { stepsDone } from '../selectors/operations';
import styles from './StepMeter.module.css';

/** The **Step** meter: one segment per step. See DESIGN.md, "Console strip" and "The step meter (edge)". */
export function StepMeter({ op, variant }: { op: Operation; variant?: 'edge' }) {
  const label = `${stepsDone(op)} of ${op.steps.length} steps done`;
  return (
    <span
      className={styles.meter}
      data-variant={variant}
      role="img"
      aria-label={label}
      title={label}
    >
      {op.steps.map((step) => (
        <span key={step.name} className={styles.segment} data-state={step.state} />
      ))}
    </span>
  );
}
