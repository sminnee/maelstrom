import { useEffect, useState } from 'react';
import { useAppStore } from '../store/store';
import styles from './ConnectionBanner.module.css';

/** How long a drop stays silent: a short reconnect is normal and needs no alarm. */
export const SILENT_DROP_MS = 3_000;
/** How long a drop shows in grey before it turns orange: past it, the screen is stale. */
export const STALE_DROP_MS = 15_000;

type Phase = 'silent' | 'reconnecting' | 'stale';

/**
 * What the change stream is doing, when it is not simply live. With nothing
 * loaded yet the page is connecting. With a world on screen, a drop shows
 * nothing for {@link SILENT_DROP_MS}, then a grey "Reconnecting…", then, past
 * {@link STALE_DROP_MS}, an orange bar that says the screen is the last known
 * state.
 */
export function ConnectionBanner({ hasData }: { hasData: boolean }) {
  const connection = useAppStore((s) => s.connection);
  const phase = useDropPhase(hasData && connection !== 'live');
  if (connection === 'live' || (hasData && phase === 'silent')) return null;
  const text = !hasData
    ? 'Connecting…'
    : phase === 'reconnecting'
      ? 'Reconnecting…'
      : 'Reconnecting… showing the last known state';
  const quiet = !hasData || phase === 'reconnecting';
  return (
    <div className={styles.banner} role="status" data-tone={quiet ? 'quiet' : undefined}>
      {text}
    </div>
  );
}

/** How long the current drop has lasted, as a phase; `silent` when there is none. */
function useDropPhase(dropped: boolean): Phase {
  const [phase, setPhase] = useState<Phase>('silent');
  useEffect(() => {
    if (!dropped) return;
    const reconnecting = setTimeout(() => setPhase('reconnecting'), SILENT_DROP_MS);
    const stale = setTimeout(() => setPhase('stale'), STALE_DROP_MS);
    return () => {
      clearTimeout(reconnecting);
      clearTimeout(stale);
      // The next drop starts silent again.
      setPhase('silent');
    };
  }, [dropped]);
  return dropped ? phase : 'silent';
}
