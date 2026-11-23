/**
 * `/api`, or `/api/hosts/<name>` for a **Remote host**'s host-scoped routes.
 * See "By name" in `docs/dev/remote-hosts.md`.
 */
export function hostPrefix(host?: string): string {
  return host ? `/api/hosts/${encodeURIComponent(host)}` : '/api';
}
