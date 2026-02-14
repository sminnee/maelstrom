/**
 * NDJSON transport layer over Node.js net.Socket.
 *
 * Handles line-delimited JSON framing: buffers incoming data,
 * splits on newlines, parses JSON, and serializes outbound messages.
 */

import { EventEmitter } from "node:events";
import type { Socket } from "node:net";

export interface TransportEvents {
  message: [msg: unknown];
  close: [];
  error: [err: Error];
}

export class NdjsonTransport extends EventEmitter<TransportEvents> {
  private buffer = "";

  constructor(private socket: Socket) {
    super();
    socket.on("data", (chunk: Buffer) => this.onData(chunk));
    socket.on("close", () => this.emit("close"));
    socket.on("error", (err: Error) => this.emit("error", err));
  }

  private onData(chunk: Buffer): void {
    this.buffer += chunk.toString("utf8");
    let newlineIndex: number;
    while ((newlineIndex = this.buffer.indexOf("\n")) !== -1) {
      const line = this.buffer.slice(0, newlineIndex).trim();
      this.buffer = this.buffer.slice(newlineIndex + 1);
      if (line.length > 0) {
        try {
          this.emit("message", JSON.parse(line));
        } catch {
          this.emit("error", new Error(`Invalid JSON: ${line.slice(0, 200)}`));
        }
      }
    }
  }

  send(msg: object): void {
    if (!this.socket.destroyed) {
      this.socket.write(JSON.stringify(msg) + "\n");
    }
  }

  close(): void {
    this.socket.end();
  }
}
