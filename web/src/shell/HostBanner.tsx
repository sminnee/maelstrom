import { useHost } from '../api/host';
import styles from './ConnectionBanner.module.css';

/**
 * Says when the agent host has stopped answering the server. The agents on
 * screen are then the last known ones: the server never exits an agent for
 * the host being away, so a daemon restart shows as this banner and then the
 * same agents again, not as a canvas full of exits.
 *
 * Nothing starts the daemon on the user's behalf, so the banner names the
 * command that does.
 */
export function HostBanner() {
  const { data } = useHost();
  const host = data?.host;
  if (!host || host.reachable) return null;
  const since = new Date(host.since);
  const at = Number.isNaN(since.getTime())
    ? host.since
    : since.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return (
    <div className={styles.banner} role="status" data-host="unreachable">
      Agent host unreachable since {at}, showing the last known agents. Run{' '}
      <code>mael self-env start</code>.
    </div>
  );
}
