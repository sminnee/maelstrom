import { Markdown } from '../markdown/Markdown';
import type { MessageItem } from '../protocol/transcript';
import styles from './RecentMessages.module.css';

/**
 * Draws the items only. Each surface supplies the box around them, because
 * each bounds the height in its own way.
 */
export function RecentMessages({ items }: { items: MessageItem[] }) {
  return items.map((item) => (
    <Markdown key={item.id} source={item.markdown} className={styles.said} />
  ));
}
