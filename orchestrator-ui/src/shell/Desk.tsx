import { DeckList } from '../deck/DeckList';
import { DeskKanban } from '../deck/DeskKanban';
import { SlotCard } from '../deck/SlotCard';
import { useLoc } from '../nav/useNav';
import { DeskModeSwitch } from './DeskModeSwitch';
import { Canvas } from '../canvas/Canvas';
import styles from './Desk.module.css';

/**
 * The desk on the wide and medium layouts: the **Desk mode** switch, then the mode itself.
 * Kanban and list have no board to grow a card on, so their card opens at the slot's right edge.
 */
export function Desk() {
  const { mode } = useLoc();
  return (
    <div className={styles.desk}>
      <DeskModeSwitch />
      <div className={styles.view}>
        {mode === 'graph' ? <Canvas /> : mode === 'kanban' ? <DeskKanban /> : <DeckList />}
        {mode !== 'graph' && <SlotCard />}
      </div>
    </div>
  );
}
