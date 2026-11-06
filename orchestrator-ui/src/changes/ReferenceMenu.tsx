import { useEffect, useLayoutEffect, useRef } from 'react';
import type { CodeTarget } from '../protocol/entities';
import { useAnchorName } from '../ui/useAnchorName';
import { usePopoverMenu } from '../ui/usePopoverMenu';
import menuStyles from '../ui/popoverMenu.module.css';
import styles from './ReferenceMenu.module.css';

/** A **Reference menu** to open: the clicked name, its token, and each use of it in the rev. */
export interface ReferenceList {
  name: string;
  token: HTMLElement;
  references: CodeTarget[];
  /** The uses past the server's cap. */
  more?: number;
}

/** `Order.total — orders.py:12`: a use, by the symbol that holds it and its line. */
function referenceLabel(r: CodeTarget): string {
  const at = `${r.path}:${r.anchor ?? r.range.start}`;
  return r.symbol ? `${r.symbol} — ${at}` : at;
}

/**
 * The **Reference menu**: each use of a name, opened beside the name's token.
 *
 * It opens as it mounts. A pick, Escape or a click outside closes it, and
 * `onClose` then unmounts it.
 */
export function ReferenceMenu({
  list,
  onPick,
  onClose,
}: {
  list: ReferenceList;
  onPick: (target: CodeTarget) => void;
  onClose: () => void;
}) {
  const { anchorName, anchorStyle } = useAnchorName();
  const menu = usePopoverMenu();
  const shown = useRef(false);
  useLayoutEffect(() => {
    const { token } = list;
    token.style.setProperty('anchor-name', anchorName);
    return () => {
      token.style.removeProperty('anchor-name');
    };
  }, [list, anchorName]);
  // After the hook's own effect, which listens for the popover's toggle.
  const { mounted, show } = menu;
  useEffect(() => {
    if (mounted) show();
  }, [mounted, show]);
  // A light dismiss closes the popover with no handler here: its state says so.
  useEffect(() => {
    if (menu.open) shown.current = true;
    else if (shown.current) onClose();
  }, [menu.open, onClose]);
  const { more } = list;
  return (
    <div
      {...menu.menuProps}
      aria-labelledby={undefined}
      aria-label={`Uses of ${list.name}`}
      className={`${menuStyles.menu} ${styles.menu}`}
      style={anchorStyle}
    >
      {list.references.map((r) => (
        <button
          key={`${r.path}:${r.anchor ?? r.range.start}`}
          type="button"
          role="menuitem"
          className={menuStyles.item}
          onClick={() => {
            menu.close();
            onPick(r);
          }}
        >
          {referenceLabel(r)}
        </button>
      ))}
      {more ? (
        <div role="menuitem" aria-disabled="true" className={`${menuStyles.item} ${styles.more}`}>
          {more} more not shown
        </div>
      ) : null}
    </div>
  );
}
