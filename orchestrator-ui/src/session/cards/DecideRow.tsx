import { useState } from 'react';
import { actionIcon } from '../../ui/actionIcons';
import { AppButton } from '../../ui/AppButton';
import { TextArea } from '../../ui/TextArea';
import styles from './cards.module.css';

/** `remember` — see `CONTEXT.md`, "Remembered approval". */
export type Decision = 'approve' | 'remember' | 'deny';

/**
 * The acts a decision offers: approve it, or deny it with a reason.
 * A permission request and a plan review ask the same thing of the user, so
 * they ask it in the same shape. A permission the agent can remember adds a
 * third: approve and remember, labelled with what it remembers.
 *
 * A comment in the field turns Approve and remember into Deny, so the comment is never lost —
 * see `orchestrator-ui/DESIGN.md`, "Review Dock".
 */
export function DecideRow({
  onDecide,
  remember,
}: {
  /** A deny carries the reason the agent gets as its tool result. */
  onDecide?: (decision: Decision, reason: string) => void | Promise<unknown>;
  /** The label of the remember button; no button without one. */
  remember?: string;
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
          <>
            {remember && (
              <AppButton
                key="remember"
                icon={actionIcon('approve')}
                disabled={!onDecide}
                onClick={() => onDecide?.('remember', '')}
              >
                {remember}
              </AppButton>
            )}
            <AppButton
              key="approve"
              icon={actionIcon('approve')}
              variant="primary"
              disabled={!onDecide}
              onClick={() => onDecide?.('approve', '')}
            >
              Approve
            </AppButton>
          </>
        )}
      </div>
    </div>
  );
}
