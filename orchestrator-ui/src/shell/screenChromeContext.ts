import { createContext, useContext } from 'react';

/**
 * What a pushed screen of the narrow layout puts in the shell's chrome: a
 * title and actions in the screen strip, and its secondary content in the side
 * sheet. See DESIGN.md, "The One Strip Rule".
 *
 * `MobileShell` owns the state and the two DOM targets. A screen reaches them
 * through `<ScreenStrip>` and `<ScreenSheet>` in `ScreenChrome.tsx`, which
 * portal into the targets.
 */
export interface ScreenChrome {
  /** The screen's own strip title, over the shell's default. */
  title: string | null;
  setTitle: (title: string | null) => void;
  /** Where the strip draws a screen's actions. */
  actions: HTMLElement | null;
  /** Where the side sheet draws a screen's content. Set only while it is open. */
  sheet: HTMLElement | null;
  sheetOpen: boolean;
  openSheet: () => void;
  closeSheet: () => void;
}

export const ScreenChromeContext = createContext<ScreenChrome | null>(null);

/**
 * The chrome of the pushed screen around the caller, or `null` where there is
 * none: the wide layout, a panel, the deck. Only `MobileShell` provides it, and
 * only for a pushed screen; the narrow layout alone mounts `MobileShell`.
 */
export function useScreenChrome(): ScreenChrome | null {
  return useContext(ScreenChromeContext);
}
