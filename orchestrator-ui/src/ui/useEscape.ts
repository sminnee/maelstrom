import { useEffect } from 'react';

/**
 * Call `onEscape` on Esc, while `active`. The panel handles its own Esc, in a composer or an
 * input, so an Esc inside the panel is left to it.
 */
export function useEscape(onEscape: () => void, active = true) {
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if ((e.target as Element | null)?.closest?.('[data-testid="panel"]')) return;
      onEscape();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onEscape, active]);
}
