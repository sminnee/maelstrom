import type { WorktreeGate } from '../protocol/entities';
import { useAppStore } from '../store/store';
import styles from './GateLozenge.module.css';

/**
 * The **Gate lozenge**. See CONTEXT.md and DESIGN.md, "Gate lozenge".
 */
export function GateLozenge({
  gate,
  interactive = true,
  className,
}: {
  gate: WorktreeGate;
  /**
   * Set false where the lozenge sits inside a link or a button, which may not
   * nest one. The reading survives; only the click goes.
   */
  interactive?: boolean;
  className?: string;
}) {
  const openGates = useAppStore((s) => s.openGates);
  const queued = gate.state === 'queued';
  const text = queued ? `queued ${gate.queuePosition ?? '?'}` : (gate.gate ?? 'gates');
  const reading = queued ? text : gate.gate ? `${gate.gate} ${gate.state}` : gate.state;
  const shared = {
    className: `${styles.chip} ${className ?? ''}`,
    'data-state': gate.state,
    'data-throb': gate.state === 'running' || undefined,
  };
  if (!interactive) {
    return (
      <span {...shared} aria-label={`gate job ${gate.jobId}: ${reading}`}>
        {text}
      </span>
    );
  }
  return (
    <button
      type="button"
      {...shared}
      aria-label={`gate job ${gate.jobId}: ${reading}, open the gate jobs`}
      onClick={(event) => {
        // The node under it opens on a click too.
        event.stopPropagation();
        openGates(gate.jobId);
      }}
    >
      {text}
    </button>
  );
}
