/**
 * The part of a WebSocket the streams use, so a test can drive a fake one.
 * A real `WebSocket` satisfies it as-is. Nothing is sent: the transcript
 * socket carries frames one way.
 */
export interface SocketLike {
  close(): void;
  onopen: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  onerror: (() => void) | null;
}

/** A `WebSocket` on a same-origin path, over `ws:` or `wss:` to match the page. */
export function browserSocket(path: string): SocketLike {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return new WebSocket(`${scheme}://${window.location.host}${path}`) as unknown as SocketLike;
}
