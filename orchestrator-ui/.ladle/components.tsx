import { useLayoutEffect } from 'react';
import type { GlobalProvider } from '@ladle/react';
import '../src/styles/tokens.css';
import '../src/styles/base.css';
import '../src/styles/text.css';

const LIGHT = '(prefers-color-scheme: light)';
/** The blocks that read `LIGHT` in the source, whatever their media text is now. */
const lightBlocks = new WeakSet<CSSMediaRule>();

/**
 * Point every `prefers-color-scheme: light` block at Ladle's theme control.
 * The tokens follow the OS scheme, and Ladle's control does not change what
 * that query reports. `auto` gives each block its own query back.
 */
function followTheme(theme: string) {
  const media = theme === 'light' ? 'all' : theme === 'dark' ? 'not all' : LIGHT;
  for (const sheet of Array.from(document.styleSheets)) {
    let rules: CSSRuleList;
    try {
      rules = sheet.cssRules;
    } catch {
      continue; // A cross-origin sheet: not ours.
    }
    for (const rule of Array.from(rules)) {
      if (!(rule instanceof CSSMediaRule)) continue;
      if (rule.media.mediaText === LIGHT) lightBlocks.add(rule);
      if (lightBlocks.has(rule)) rule.media.mediaText = media;
    }
  }
}

/**
 * Stories run against the app's real tokens and base styles, so what a story
 * shows is what the app draws. Without this they would render on Ladle's own
 * white page and prove nothing.
 */
export const Provider: GlobalProvider = ({ children, globalState }) => {
  useLayoutEffect(() => followTheme(globalState.theme), [globalState.theme]);
  return (
    <div style={{ background: 'var(--bg)', color: 'var(--fg)', minHeight: '100vh' }}>
      {children}
    </div>
  );
};
