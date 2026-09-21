import type { MessageItem } from '../../protocol/transcript';
import { Markdown } from '../../markdown/Markdown';
import styles from './cards.module.css';

/**
 * One turn in the session stream. Low attention carries working detail; high
 * prose remains at the reading rank.
 */
export function AgentMessage({ item }: { item: MessageItem }) {
  return (
    <div className={styles.message} data-role={item.role} data-pending={item.pending || undefined}>
      {item.role === 'user' && <span className="srOnly">you</span>}
      <Markdown source={item.markdown} />
    </div>
  );
}
