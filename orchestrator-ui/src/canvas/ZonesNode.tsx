import type { NodeProps, Node } from '@xyflow/react';
import type { ZoneBand } from './layout';
import styles from './ZonesNode.module.css';

export type ZonesFlowNode = Node<{ zones: ZoneBand[] }, 'zones'>;

/** What each zone is called, in the operator's words. */
const ZONE_LABELS = {
  done: 'Done',
  running: 'Running',
  notStarted: 'Not started',
} as const;

/**
 * One strip of zone labels for the whole board, above every lane. A node
 * rather than a fixed overlay, so the labels pan and zoom with the board and
 * stay over the columns they name. A collapsed zone draws none: it holds no
 * column, so its label would sit on the next zone's.
 */
export function ZonesNode({ data }: NodeProps<ZonesFlowNode>) {
  return (
    <div className={styles.zones} data-testid="zones-node">
      {data.zones
        .filter((band) => band.columns > 0)
        .map((band) => (
          <span
            key={band.zone}
            className={styles.label}
            data-testid="zone-label"
            data-zone={band.zone}
            style={{ left: `${band.x}px` }}
          >
            {ZONE_LABELS[band.zone]}
          </span>
        ))}
    </div>
  );
}
