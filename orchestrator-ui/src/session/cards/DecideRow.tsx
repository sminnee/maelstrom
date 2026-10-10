import { useState } from 'react';
import { actionIcon } from '../../ui/actionIcons';
import { AppButton } from '../../ui/AppButton';
import { TextArea } from '../../ui/TextArea';
import styles from './cards.module.css';

/**
 * The two acts a binary decision offers: approve it, or deny it with a reason.
 * A permission request and a plan review ask the same thing of the user, so
 * they ask it in the same shape.
 *
 * A comment in the field turns Approve into Deny, so the comment is never lost —
 * see `orchestrator-ui/DESIGN.md`, "Review Dock".
 */
export function DecideRow({
  onDecide,
}: {
  /** A deny carries the reason the agent gets as its tool result. */
  onDecide?: (decision: 'approve' | 'deny', reason: string) => void | Promise<unknown>;
}) {
  const [reason, setReason] = useState('');
  const denying = reason.trim() !== '';
  return (
    <div className={`${styles.options} ${styles.decide}`} data-role="prompt-actions">
      <TextArea
        grow
        rows={1}
        className={styles.reasonInput}
        aria-label="Deny reason"
        placeholder="Comment to deny"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <div className={styles.buttons}>
        {denying ? (
          <AppButton
            key="deny"
            icon={actionIcon('deny')}
            variant="danger"
            disabled={!onDecide}
            onClick={() => onDecide?.('deny', reason.trim())}
          >
            Deny
          </AppButton>
        ) : (
          <AppButton
            key="approve"
            icon={actionIcon('approve')}
            variant="primary"
            disabled={!onDecide}
            onClick={() => onDecide?.('approve', '')}
          >
            Approve
          </AppButton>
        )}
      </div>
    </div>
  );
}
