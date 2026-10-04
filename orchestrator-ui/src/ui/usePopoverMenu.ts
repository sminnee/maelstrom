import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';

/** A menu's items: a `menuitem` and a `menuitemcheckbox` both take the keyboard. */
const menuItems = (menu: HTMLElement | null) =>
  Array.from(menu?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? []);

/**
 * The mechanics of a menu that opens from a trigger: a `popover="auto"`, so a
 * click outside or Escape closes it.
 *
 * Spread `triggerProps` on the trigger and `menuProps` on the menu. The menu
 * takes its name from the trigger. An item with `aria-disabled="true"` is
 * skipped when the menu opens. `close()` hides the menu and gives the focus
 * back to the trigger, as Escape does.
 *
 * The menu ref is a callback ref, so a menu that mounts after the caller,
 * behind a condition, still reports its state.
 */
export function usePopoverMenu() {
  const [menu, setMenu] = useState<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const openRef = useRef(false);
  const openAtPress = useRef<boolean | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  const triggerId = useId();

  // The popover's own events are the truth for `open`: a light dismiss closes
  // it without any handler here running.
  useEffect(() => {
    if (!menu) return;
    const onToggle = (e: Event) => {
      const isOpen = (e as Event & { newState: string }).newState === 'open';
      openRef.current = isOpen;
      setOpen(isOpen);
      if (isOpen)
        menuItems(menu)
          .find((i) => i.getAttribute('aria-disabled') !== 'true')
          ?.focus();
    };
    menu.addEventListener('toggle', onToggle);
    return () => menu.removeEventListener('toggle', onToggle);
  }, [menu]);

  const hide = () => {
    if (openRef.current) menu?.hidePopover();
  };

  const close = () => {
    hide();
    triggerRef.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent) => {
    const all = menuItems(menu);
    const at = all.indexOf(document.activeElement as HTMLElement);
    const to =
      e.key === 'ArrowDown'
        ? (at + 1) % all.length
        : e.key === 'ArrowUp'
          ? (at - 1 + all.length) % all.length
          : e.key === 'Home'
            ? 0
            : e.key === 'End'
              ? all.length - 1
              : null;
    if (to !== null) {
      e.preventDefault();
      all[to]?.focus();
    } else if (e.key === 'Escape') {
      // The Escape is the menu's: a card that collapses on Escape must not
      // hear it too.
      e.preventDefault();
      e.stopPropagation();
      close();
    }
  };

  return {
    open,
    close,
    menuId,
    triggerProps: {
      ref: triggerRef,
      id: triggerId,
      'aria-haspopup': 'menu' as const,
      'aria-expanded': open,
      'aria-controls': menuId,
      // A click outside an open popover closes it before `click` fires, so
      // the trigger reads the state as the press began: a click that closed
      // the menu must not open it again.
      onPointerDown: () => {
        openAtPress.current = openRef.current;
      },
      onClick: () => {
        const wasOpen = openAtPress.current ?? openRef.current;
        openAtPress.current = null;
        if (wasOpen) hide();
        else menu?.showPopover();
      },
    },
    menuProps: {
      ref: setMenu,
      id: menuId,
      role: 'menu' as const,
      'aria-labelledby': triggerId,
      popover: 'auto' as const,
      onKeyDown,
    },
  };
}
