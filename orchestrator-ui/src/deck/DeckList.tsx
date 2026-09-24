import { useWorld } from '../api/useWorld';
import { ZONES } from '../protocol/progress';
import { emptyZoneWords, zoneLabel } from '../selectors/deck';
import { useDeck } from './useDeck';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import { DeckRow } from './DeckRow';
import styles from './DeckList.module.css';

/** The rows the zone tabs control, so a screen reader can follow the pair. */
const DECK_PANEL_ID = 'deck-rows';

/** The narrow layout's main view: the desk as a list, tabbed by zone. */
export function DeckList() {
  const { status, errors, retry } = useWorld();
  const deck = useDeck();
  const zone = useAppStore((s) => s.ui.deckZone);
  const setDeckZone = useAppStore((s) => s.setDeckZone);
  const pushScreen = useAppStore((s) => s.pushScreen);

  if (status === 'loading') {
    return (
      <div className={styles.frame} data-testid="deck-loading">
        Loading the world…
      </div>
    );
  }
  if (status === 'error') {
    return (
      <div className={styles.frame} role="alert" data-testid="deck-error">
        <div>Could not load the world: {errors[0]?.message ?? 'unknown error'}</div>
        <AppButton onClick={retry}>Retry</AppButton>
      </div>
    );
  }

  const rows = deck.zones[zone];
  return (
    <div className={styles.deck} data-testid="deck-list">
      <div className={styles.tabs} role="tablist" aria-label="Progress">
        {ZONES.map((z) => (
          <button
            key={z}
            type="button"
            role="tab"
            className={styles.tab}
            aria-selected={z === zone}
            aria-controls={DECK_PANEL_ID}
            tabIndex={z === zone ? 0 : -1}
            data-zone={z}
            onClick={() => setDeckZone(z)}
          >
            {zoneLabel(z)}
            <span className={styles.count}>{deck.counts[z]}</span>
          </button>
        ))}
      </div>
      <div className={styles.rows} role="tabpanel" id={DECK_PANEL_ID}>
        {rows.length === 0 ? (
          <p className={styles.empty} data-testid="deck-empty">
            {emptyZoneWords(zone)}
          </p>
        ) : (
          rows.map((node) => (
            <DeckRow
              key={node.id}
              node={node}
              onOpen={() => pushScreen({ kind: 'detail', nodeId: node.id })}
            />
          ))
        )}
      </div>
    </div>
  );
}
