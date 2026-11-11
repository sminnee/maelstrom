import { useWorld } from '../api/useWorld';
import type { Worktree } from '../protocol/entities';
import { worktreeTabOffers, type Offer } from '../selectors/worktreeTabs';
import { PanelLink } from '../shell/PanelLink';
import { actionIcon } from '../ui/actionIcons';
import { useAnchorName } from '../ui/useAnchorName';
import { useNow } from '../ui/useNow';
import { usePopoverMenu } from '../ui/usePopoverMenu';
import menuStyles from '../ui/popoverMenu.module.css';
import styles from './TabPicker.module.css';

/**
 * The **Tab picker**: a `+` at the end of the strip, with a menu of each tab
 * worktree `worktree` can show. A choice opens the tab as a panel link does.
 */
export function TabPicker({ worktree, openKeys }: { worktree: Worktree; openKeys: string[] }) {
  const { world } = useWorld();
  const now = useNow();
  const menu = usePopoverMenu();
  const { anchorStyle } = useAnchorName();
  const offers = worktreeTabOffers(world, worktree, now);

  const item = (offer: Offer) => {
    const open = openKeys.includes(offer.tab.key);
    return (
      <PanelLink
        key={offer.tab.key}
        tab={offer.tab}
        role="menuitem"
        aria-current={open}
        icon={false}
        className={`${menuStyles.item} ${styles.item}`}
      >
        <span className={styles.mark} aria-hidden="true">
          {open && actionIcon('approve')}
        </span>
        {offer.prefix && <span className={styles.prefix}>{offer.prefix}</span>}
        <span className="truncate">{offer.label}</span>
      </PanelLink>
    );
  };
  const section = (heading: string, items: Offer[]) =>
    items.length > 0 && (
      <div role="group" aria-label={heading} className={styles.group}>
        <span className={styles.heading} aria-hidden="true">
          {heading}
        </span>
        {items.map(item)}
      </div>
    );

  return (
    <>
      <button
        {...menu.triggerProps}
        type="button"
        className={styles.trigger}
        style={anchorStyle}
        aria-label="Open a tab"
      >
        {actionIcon('new')}
      </button>
      {/* A pick closes the menu before the link opens its tab. A click on a
          heading is not a pick. */}
      <div
        {...menu.menuProps}
        className={`${menuStyles.menu} ${styles.menu}`}
        style={anchorStyle}
        onClickCapture={(e) => {
          if ((e.target as Element).closest('[role="menuitem"]')) menu.close();
        }}
      >
        {offers.changes && item({ tab: offers.changes, label: 'Changes', prefix: null })}
        {section('Sessions', offers.sessions)}
        {section('Documents', offers.documents)}
      </div>
    </>
  );
}
