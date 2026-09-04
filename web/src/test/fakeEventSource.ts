import { CLOSED, CONNECTING, OPEN, type EventSourceLike } from '../live/changeStream';

/**
 * An `EventSource` the test drives by hand: `open` connects it and sends the
 * reset every connection starts with, `emit` sends a named event, `fail`
 * drops it the way the browser reports a drop.
 */
export class FakeEventSource implements EventSourceLike {
  static readonly CONNECTING = CONNECTING;
  static readonly OPEN = OPEN;
  static readonly CLOSED = CLOSED;

  readonly url: string;
  readyState = CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, Set<(event: { data: string }) => void>>();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, listener: (event: { data: string }) => void): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }

  close(): void {
    this.readyState = CLOSED;
  }

  /** Connect, and send the `reset` the server opens every stream with. */
  open(epoch = 'e1'): void {
    this.readyState = OPEN;
    this.onopen?.();
    this.emit('reset', { epoch });
  }

  emit(type: string, data: unknown): void {
    const event = { data: JSON.stringify(data) };
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  /**
   * Drop the connection. `'connecting'` is the browser retrying by itself;
   * `'closed'` is the browser having given up.
   */
  fail(how: 'connecting' | 'closed'): void {
    this.readyState = how === 'closed' ? CLOSED : CONNECTING;
    this.onerror?.();
  }
}
