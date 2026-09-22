import { tabAttribution } from '../selectors/tabs';
import { useWorld } from '../api/useWorld';
import { useAppStore } from '../store/store';
import { CloseIcon } from '../shell/CloseIcon';
import { TabChip } from './TabChip';
import styles from './PanelTabs.module.css';

export const PANEL_BODY_ID = 'panel-body';

export function PanelTabs() {
  const { world } = useWorld();
  const tabs = useAppStore((s) => s.ui.tabs);
  const activeTabKey = useAppStore((s) => s.ui.activeTabKey);
  const activateTab = useAppStore((s) => s.activateTab);
  const closeTab = useAppStore((s) => s.closeTab);

  // One tab stop for the strip; arrows move between tabs.
  const onKeyDown = (e: React.KeyboardEvent, index: number) => {
    const key = tabs[index]?.key;
    if (!key) return;
    if (e.key === 'Enter' || e.key === ' ') activateTab(key);
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      const next = tabs[(index + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
      if (next) {
        activateTab(next.key);
        (e.currentTarget.parentElement?.children[tabs.indexOf(next)] as HTMLElement)?.focus();
      }
    }
  };

  return (
    <div className={styles.strip} role="tablist">
      {tabs.map((tab, index) => {
        const attribution = tabAttribution(world, tab);
        const active = tab.key === activeTabKey;
        return (
          <div
            key={tab.key}
            role="tab"
            aria-selected={active}
            aria-controls={PANEL_BODY_ID}
            tabIndex={active ? 0 : -1}
            className={styles.tab}
            data-active={active || undefined}
            data-tab-key={tab.key}
            data-phase={attribution.phase ?? undefined}
            // The name is pinned rather than computed. A tab's contents win
            // over its `title`, and the close button's own label joins them,
            // so the computed name reads "NORT-7 Close NORT-7".
            aria-label={[attribution.id, attribution.label].filter(Boolean).join(' ')}
            // The strip has no room for the task's title, so the tooltip carries it.
            title={attribution.title || undefined}
            onClick={() => activateTab(tab.key)}
            onKeyDown={(e) => onKeyDown(e, index)}
          >
            <TabChip attribution={attribution} />
            {attribution.label && <span className={styles.label}>{attribution.label}</span>}
            <button
              type="button"
              className={styles.close}
              aria-label={['Close', attribution.label, attribution.id].filter(Boolean).join(' ')}
              onClick={(e) => {
                e.stopPropagation();
                closeTab(tab.key);
              }}
            >
              <CloseIcon />
            </button>
          </div>
        );
      })}
    </div>
  );
}
