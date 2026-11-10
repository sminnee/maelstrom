import { useCard } from '../nav/useCard';
import { actionIcon } from '../ui/actionIcons';
import styles from './CollapseButton.module.css';

/** The × at a card's top right. It collapses the open card. */
export function CollapseButton() {
  const { collapse } = useCard();
  return (
    <button type="button" className={styles.close} aria-label="Collapse" onClick={() => collapse()}>
      {actionIcon('close')}
    </button>
  );
}
