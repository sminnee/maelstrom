import type { MessageItem } from '../../protocol/transcript';
import { Markdown } from '../../markdown/Markdown';
import styles from './cards.module.css';

/**
 * One turn in the session stream. The assistant's prose is demoted so that a
 * callout it marked reads at full rank; the operator's turn never is — see
 * DESIGN.md, "The Two Ranks of Prose Rule".
 */
export function AgentMessage({ item }: { item: MessageItem }) {
  const demoted = item.role !== 'user';
  return (
    <div className={styles.message} data-role={item.role}>
      <div className={styles.role}>{item.role === 'user' ? 'you' : 'agent'}</div>
      {/* The rank is carried in CSS, which the test runner cannot see. The
          attribute is what makes it assertable. */}
      <Markdown
        source={item.markdown}
        className={demoted ? styles.demoted : undefined}
        data-prose-rank={demoted ? 'demoted' : 'full'}
      />
    </div>
  );
}
