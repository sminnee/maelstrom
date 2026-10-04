import { type ReactNode, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { useScreenChrome } from './screenChromeContext';

/** The screen's title and actions, drawn in the strip. */
export function ScreenStrip({ title, children }: { title?: string; children?: ReactNode }) {
  const chrome = useScreenChrome();
  const setTitle = chrome?.setTitle;
  useLayoutEffect(() => {
    if (!setTitle || title === undefined) return;
    setTitle(title);
    return () => setTitle(null);
  }, [setTitle, title]);
  return chrome?.actions && children ? createPortal(children, chrome.actions) : null;
}

/** The screen's secondary content, drawn in the side sheet while it is open. */
export function ScreenSheet({ children }: { children: ReactNode }) {
  const chrome = useScreenChrome();
  return chrome?.sheet ? createPortal(children, chrome.sheet) : null;
}
