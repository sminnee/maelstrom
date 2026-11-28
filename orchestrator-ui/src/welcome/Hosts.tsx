import { useClient } from '../api/client';
import { useHost } from '../api/host';
import type { RemoteHostState } from '../protocol/entities';
import { clockTime } from '../protocol/time';
import { useNow } from '../ui/useNow';
import styles from './WelcomeDialog.module.css';

/**
 * The Welcome dialog's Hosts row. The last line names the host this browser
 * is on, because that decides whether the cmux control shows at all.
 */
export function Hosts() {
  const host = useHost().data?.host;
  const client = useClient().data;
  const now = useNow();
  if (!host) return <>…</>;
  const own = host.name ? `${host.name} (this server)` : 'this server';
  const lines = [
    [own, ...facts(host.cmux, host.ssh)].join(' · '),
    ...host.remoteHosts.map((remote) => remoteWords(remote, now)),
  ];
  return (
    <div className={styles.hosts}>
      {lines.map((line) => (
        <span key={line}>{line}</span>
      ))}
      {client && (
        <span className={styles.faint}>{browserWords(client, host.name || 'this server')}</span>
      )}
    </div>
  );
}

function remoteWords(remote: RemoteHostState, now: number): string {
  const since = clockTime(remote.since, now);
  if (remote.online) {
    return [remote.name, `online since ${since}`, ...facts(remote.cmux, remote.ssh)].join(' · ');
  }
  if (!remote.reason) return `${remote.name} · connecting…`;
  const offline = since ? `offline since ${since}` : 'offline';
  return `${remote.name} · ${offline} — ${remote.reason}`;
}

/** The cmux and SSH words. A fact not yet read says nothing. */
function facts(cmux: boolean | null, ssh: boolean | null): string[] {
  return [
    cmux === null ? '' : cmux ? 'cmux' : 'no cmux',
    ssh === null ? '' : ssh ? 'SSH' : 'no SSH: set dev_host',
  ].filter(Boolean);
}

function browserWords(client: { local: boolean; host: string }, own: string): string {
  if (client.local) return `This browser is on ${own}`;
  if (client.host) return `This browser is on ${client.host}`;
  return 'This browser is on no known host, so no cmux links show';
}
