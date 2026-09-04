import type { SocketLike } from '../live/socketLike';

/**
 * A WebSocket the test drives by hand. `open` connects it, `receive`
 * delivers one message, `serverClose` closes it with a code the way the
 * server would.
 */
export class FakeSocket implements SocketLike {
  readonly url: string;
  closed = false;
  /** Set by the fake server once it has answered the open. */
  opened = false;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
  }

  /** The agent id the URL names. */
  get agentId(): string {
    return this.url.match(/\/api\/agents\/([^/?]+)\/stream/)?.[1] ?? '';
  }

  /** The `from` cursor the URL carries, or null. */
  get from(): number | null {
    const raw = this.url.match(/[?&]from=(\d+)/)?.[1];
    return raw === undefined ? null : Number(raw);
  }

  /** The page closing it. */
  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.onclose?.({ code: 1000 });
  }

  open(): void {
    this.onopen?.();
  }

  receive(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  /** The server closing it, or the connection dropping (1006). */
  serverClose(code = 1006): void {
    if (this.closed) return;
    this.closed = true;
    this.onclose?.({ code });
  }
}
