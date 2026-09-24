/**
 * Where the change stream is: the orchestrator directly, not the dev server's
 * `/api` proxy, which would hold the stream open with no page behind it.
 * The host comes from the page so a tailnet page reaches its own server.
 * See docs/dev/orchestrator-ui.md.
 */
export function eventsUrl(
  location: { protocol: string; hostname: string; port: string },
  orchestratorPort: string | undefined,
): string {
  // Nothing to reach past the origin — a build the orchestrator serves itself
  // has one port, and a relative URL is what keeps it working.
  if (!orchestratorPort || orchestratorPort === location.port) return '/api/events';
  return `${location.protocol}//${location.hostname}:${orchestratorPort}/api/events`;
}
