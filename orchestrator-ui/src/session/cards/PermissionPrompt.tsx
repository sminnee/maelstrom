import type { PermissionRequestItem } from '../../protocol/transcript';
import { DecideRow } from './DecideRow';
import { ToolInput } from './ToolCallCard';
import styles from './cards.module.css';

export function PermissionPrompt({
  item,
  onDecide,
}: {
  item: PermissionRequestItem;
  onDecide?: (decision: 'approve' | 'deny', reason: string) => void | Promise<unknown>;
}) {
  return (
    <div
      className={styles.prompt}
      data-decision={item.decision}
      data-stale={item.stale || undefined}
    >
      <div className={styles.qhead} data-role="prompt-head">
        Permission · {item.tool}
      </div>
      <div data-role="prompt-text">{item.description || item.tool}</div>
      <div data-role="prompt-detail">
        <ToolInput tool={item.tool} input={item.input} />
      </div>
      {item.decision ? (
        <div className={styles.answer}>
          {item.decision === 'allow' ? 'allowed' : 'denied'}
          {item.reason ? ` · ${item.reason}` : ''}
        </div>
      ) : item.stale ? (
        <div className={styles.answer}>no longer pending</div>
      ) : (
        <DecideRow onDecide={onDecide} />
      )}
    </div>
  );
}
