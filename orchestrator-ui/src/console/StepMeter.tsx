import type { Operation } from '../protocol/entities';
import { stepsDone } from '../selectors/operations';
import styles from './StepMeter.module.css';

/** The **Step** meter: one segment per step. See DESIGN.md, "Console strip". */
export function StepMeter({ op }: { op: Operation }) {
  const label = `${stepsDone(op)} of ${op.steps.length} steps done`;
  return (
    <span className={styles.meter} role="img" aria-label={label} title={label}>
      {op.steps.map((step) => (
        <span key={step.name} className={styles.segment} data-state={step.state} />
      ))}
    </span>
  );
}
