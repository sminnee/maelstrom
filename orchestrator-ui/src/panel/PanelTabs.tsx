import { tabAttribution } from '../selectors/tabs';
import { useWorld } from '../api/useWorld';
import { useAppStore } from '../store/store';
import { actionIcon } from '../ui/actionIcons';
import { TabChip } from './TabChip';
import { usePanelNav } from './usePanelNav';
import type { TabGroup } from '../selectors/tabs';
import styles from './PanelTabs.module.css';

/** A small panel with its right half filled: this tab shows in the body's right half. */
function SplitMark() {
  return (
    <svg
      className={styles.splitMark}
      width="12"
      height="12"
      viewBox="0 0 12 12"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="1" y="1.5" width="10" height="9" rx="1.5" fill="none" stroke="currentColor" />
      <rect x="6" y="1.5" width="5" height="9" fill="currentColor" />
    </svg>
  );
}

export const PANEL_BODY_ID = 'panel-body';
/** What a sidebar row controls: the worktree bar, the strip and the body. */
export const PANEL_GROUP_ID = 'panel-group';

/** The tabs of the worktree in view. The sidebar switches between worktrees. */
export function PanelTabs({
  group,
  onClose,
}: {
  group: TabGroup | null;
  onClose: (keys: string[]) => void;
}) {
  const { world } = useWorld();
  const tabs = group?.tabs ?? [];
  const { activeTabKey, activate, split: toggleSplit } = usePanelNav();
  const splitKey = useAppStore((s) => (group ? s.ui.splitTabs[group.key] : undefined));
  // Shift puts a tab beside the active one, or takes it back out.
  const choose = (key: string, shift: boolean) => {
    if (shift) toggleSplit(key);
    else activate(key);
  };

  // One tab stop for the strip; arrows move between tabs.
  const onKeyDown = (e: React.KeyboardEvent, index: number) => {
    const key = tabs[index]?.key;
    // A key on the close button is the button's own, not the tab's.
    if (!key || e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      choose(key, e.shiftKey);
    }
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      // The split tab cannot be active, so the arrows pass over it: focus and
      // the strip's one tab stop stay on the same tab.
      const ring = tabs.filter((t, i) => i === index || t.key !== splitKey);
      const at = ring.indexOf(tabs[index]!);
      const next = ring[(at + (e.key === 'ArrowRight' ? 1 : ring.length - 1)) % ring.length];
      if (next) {
        activate(next.key);
        (e.currentTarget.parentElement?.children[tabs.indexOf(next)] as HTMLElement)?.focus();
      }
    }
  };

  return (
    <div className={styles.strip} role="tablist" aria-label="Open tabs">
      {tabs.map((tab, index) => {
        const attribution = tabAttribution(world, tab);
        const active = tab.key === activeTabKey;
        const split = tab.key === splitKey;
        return (
          <div
            key={tab.key}
            role="tab"
            // Both tabs in view are selected: the body shows them side by side.
            aria-selected={active || split}
            aria-controls={PANEL_BODY_ID}
            tabIndex={active ? 0 : -1}
            className={styles.tab}
            data-active={active || undefined}
            data-split={split || undefined}
            data-tab-key={tab.key}
            data-phase={attribution.phase ?? undefined}
            // The name is pinned rather than computed. A tab's contents win
            // over its `title`, and the close button's own label joins them,
            // so the computed name reads "NORT-7 Close NORT-7".
            aria-label={[attribution.id, attribution.label].filter(Boolean).join(' ')}
            // The strip has no room for the task's title, so the tooltip carries it.
            title={attribution.title || undefined}
            onClick={(e) => choose(tab.key, e.shiftKey)}
            onKeyDown={(e) => onKeyDown(e, index)}
          >
            <TabChip attribution={attribution} />
            {split && <SplitMark />}
            {attribution.label && <span className={styles.label}>{attribution.label}</span>}
            <button
              type="button"
              className={styles.close}
              aria-label={['Close', attribution.label, attribution.id].filter(Boolean).join(' ')}
              onClick={(e) => {
                e.stopPropagation();
                onClose([tab.key]);
              }}
            >
              {actionIcon('close')}
            </button>
          </div>
        );
      })}
    </div>
  );
}
