import { AppButton } from '../../ui/AppButton';
import styles from './comments.module.css';

/**
 * The band under the Changes tab while change comments are held. See
 * `orchestrator-ui/DESIGN.md`, "Comment Dock".
 */
export function CommentDock({
  count,
  recipients,
  onPost,
  onClear,
}: {
  count: number;
  /** A name for each agent the post reaches. */
  recipients: string[];
  onPost: () => Promise<unknown>;
  onClear: () => void;
}) {
  const held = `${count} ${count === 1 ? 'comment' : 'comments'}`;
  return (
    <div className={styles.dock} role="region" aria-label="Change comments">
      <AppButton
        variant="primary"
        disabled={recipients.length === 0}
        processingChildren="Posting"
        errorChildren={(err) => (err instanceof Error ? err.message : 'Failed')}
        onClick={onPost}
      >
        Post comments
      </AppButton>
      <span className={styles.summary}>
        {recipients.length > 0
          ? `${held} to ${recipients.join(', ')}`
          : `${held}. No agent is running in this worktree.`}
      </span>
      <AppButton variant="quiet" onClick={onClear}>
        Clear
      </AppButton>
    </div>
  );
}
