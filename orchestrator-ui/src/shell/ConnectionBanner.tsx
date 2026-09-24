import { useAppStore } from '../store/store';
import styles from './ConnectionBanner.module.css';

/**
 * What the change stream is doing, when it is not simply live. With nothing
 * loaded yet the page is connecting; with a world on screen a drop means the
 * screen is the last known state, and says so.
 */
export function ConnectionBanner({ hasData }: { hasData: boolean }) {
  const connection = useAppStore((s) => s.connection);
  if (connection === 'live') return null;
  const text =
    connection === 'connecting' && !hasData
      ? 'Connecting…'
      : 'Reconnecting… showing the last known state';
  return (
    <div className={styles.banner} role="status" data-connection={connection}>
      {text}
    </div>
  );
}
