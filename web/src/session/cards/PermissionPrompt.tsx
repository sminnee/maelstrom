import { useState } from 'react';
import type { PermissionRequestItem } from '../../protocol/transcript';
import { ToolInput } from './ToolCallCard';
import styles from './cards.module.css';

export function PermissionPrompt({
  item,
  onDecide,
}: {
  item: PermissionRequestItem;
  /** A deny carries the reason the agent gets as its tool result. */
  onDecide?: (decision: 'approve' | 'deny', reason: string) => void;
}) {
  const [reason, setReason] = useState('');
  return (
    <div
      className={styles.prompt}
      data-decision={item.decision}
      data-stale={item.stale || undefined}
    >
      <div className={styles.qhead}>Permission · {item.tool}</div>
      <div>{item.description || item.tool}</div>
      <ToolInput tool={item.tool} input={item.input} />
      {item.decision ? (
        <div className={styles.answer}>
          {item.decision === 'allow' ? 'allowed' : 'denied'}
          {item.reason ? ` · ${item.reason}` : ''}
        </div>
      ) : item.stale ? (
        <div className={styles.answer}>no longer pending</div>
      ) : (
        <div className={styles.options}>
          <button
            type="button"
            className={styles.primary}
            disabled={!onDecide}
            onClick={() => onDecide?.('approve', '')}
          >
            Approve
          </button>
          <input
            className={styles.reasonInput}
            aria-label="Deny reason"
            placeholder="Reason to deny"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <button
            type="button"
            disabled={!onDecide || !reason.trim()}
            onClick={() => onDecide?.('deny', reason.trim())}
          >
            Deny
          </button>
        </div>
      )}
    </div>
  );
}
