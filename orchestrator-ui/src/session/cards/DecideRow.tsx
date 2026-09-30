import { useState } from 'react';
import { AppButton } from '../../ui/AppButton';
import { TextArea } from '../../ui/TextArea';
import styles from './cards.module.css';

/**
 * The two acts a binary decision offers: approve it, or deny it with a reason.
 * A permission request and a plan review ask the same thing of the user, so
 * they ask it in the same shape.
 *
 * Approve leads, as the primary — see `orchestrator-ui/DESIGN.md`, "Review Dock".
 */
export function DecideRow({
  onDecide,
}: {
  /** A deny carries the reason the agent gets as its tool result. */
  onDecide?: (decision: 'approve' | 'deny', reason: string) => void | Promise<unknown>;
}) {
  const [reason, setReason] = useState('');
  return (
    <div className={`${styles.options} ${styles.decide}`} data-role="prompt-actions">
      <AppButton variant="primary" disabled={!onDecide} onClick={() => onDecide?.('approve', '')}>
        Approve
      </AppButton>
      <TextArea
        grow
        rows={1}
        className={styles.reasonInput}
        aria-label="Deny reason"
        placeholder="Reason to deny"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <AppButton
        disabled={!onDecide || !reason.trim()}
        onClick={() => onDecide?.('deny', reason.trim())}
      >
        Deny
      </AppButton>
    </div>
  );
}
