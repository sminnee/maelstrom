/**
 * Unix domain socket server that manages client connections
 * and routes NDJSON messages to/from AgentSessions.
 */

import { createServer, type Server, type Socket } from "node:net";
import { existsSync, mkdirSync, unlinkSync } from "node:fs";
import { dirname } from "node:path";
import { NdjsonTransport } from "./transport.js";
import { AgentSession } from "./session.js";
import type { InboundMessage, OutboundMessage } from "./protocol.js";

export const DEFAULT_SOCKET_PATH = "/tmp/maelstrom/agent.sock";

export class AgentServer {
  private server: Server;
  private sessions = new Map<string, AgentSession>();
  private transports = new Set<NdjsonTransport>();

  constructor(private socketPath: string = DEFAULT_SOCKET_PATH) {
    this.server = createServer((socket) => this.onConnection(socket));
  }

  /**
   * Register an externally-created session (e.g. from CLI mode).
   * Socket clients can then send messages targeting this session.
   */
  registerSession(sessionId: string, session: AgentSession): void {
    this.sessions.set(sessionId, session);
  }

  /**
   * Broadcast an outbound message to all connected socket clients.
   */
  broadcast(msg: OutboundMessage): void {
    for (const transport of this.transports) {
      transport.send(msg);
    }
  }

  async start(): Promise<void> {
    const dir = dirname(this.socketPath);
    if (!existsSync(dir)) {
      mkdirSync(dir, { recursive: true });
    }
    if (existsSync(this.socketPath)) {
      unlinkSync(this.socketPath);
    }

    return new Promise((resolve, reject) => {
      this.server.on("error", reject);
      this.server.listen(this.socketPath, () => {
        this.server.removeListener("error", reject);
        resolve();
      });
    });
  }

  async stop(): Promise<void> {
    // Close all sessions
    const closePromises = Array.from(this.sessions.values()).map((s) => s.close());
    await Promise.allSettled(closePromises);
    this.sessions.clear();

    // Close all transports
    for (const transport of this.transports) {
      transport.close();
    }
    this.transports.clear();

    // Close the server
    return new Promise((resolve) => {
      this.server.close(() => resolve());
    });
  }

  private onConnection(socket: Socket): void {
    const transport = new NdjsonTransport(socket);
    this.transports.add(transport);

    transport.on("message", (raw: unknown) => {
      const msg = raw as InboundMessage;
      if (!msg || typeof msg !== "object" || !("type" in msg) || !("sessionId" in msg)) {
        transport.send({
          sessionId: "",
          type: "error",
          error: "Invalid message: must have 'type' and 'sessionId' fields",
        } satisfies OutboundMessage);
        return;
      }
      this.handleMessage(msg, transport);
    });

    transport.on("close", () => {
      this.transports.delete(transport);
    });

    transport.on("error", (err: Error) => {
      console.error(`[transport] error: ${err.message}`);
    });
  }

  private handleMessage(msg: InboundMessage, transport: NdjsonTransport): void {
    switch (msg.type) {
      case "start_session":
        this.startSession(msg, transport);
        break;
      case "send_prompt":
        this.getSession(msg.sessionId)?.sendPrompt(msg.prompt);
        break;
      case "resume_session":
        this.resumeSession(msg, transport);
        break;
      case "permission_response":
        this.getSession(msg.sessionId)?.resolvePermission(
          msg.requestId,
          msg.behavior,
          msg.updatedInput,
          msg.message,
        );
        break;
      case "question_response":
        this.getSession(msg.sessionId)?.resolveQuestion(msg.requestId, msg.answers);
        break;
      case "interrupt":
        this.getSession(msg.sessionId)?.interrupt();
        break;
      case "close_session":
        this.closeSession(msg.sessionId, transport);
        break;
    }
  }

  private startSession(
    msg: InboundMessage & { type: "start_session" },
    transport: NdjsonTransport,
  ): void {
    if (this.sessions.has(msg.sessionId)) {
      transport.send({
        sessionId: msg.sessionId,
        type: "error",
        error: `Session '${msg.sessionId}' already exists`,
      } satisfies OutboundMessage);
      return;
    }

    const session = new AgentSession(msg.sessionId, (outMsg) => transport.send(outMsg));
    this.sessions.set(msg.sessionId, session);
    session.start(msg);
  }

  private resumeSession(
    msg: InboundMessage & { type: "resume_session" },
    transport: NdjsonTransport,
  ): void {
    // Create a new agent session that resumes a previous Claude session
    const session = new AgentSession(msg.sessionId, (outMsg) => transport.send(outMsg));
    this.sessions.set(msg.sessionId, session);
    session.resume(msg.claudeSessionId, msg.prompt, msg.cwd);
  }

  private async closeSession(
    sessionId: string,
    transport: NdjsonTransport,
  ): Promise<void> {
    const session = this.sessions.get(sessionId);
    if (session) {
      await session.close();
      this.sessions.delete(sessionId);
    } else {
      transport.send({
        sessionId,
        type: "error",
        error: `Session '${sessionId}' not found`,
      } satisfies OutboundMessage);
    }
  }

  private getSession(sessionId: string): AgentSession | undefined {
    const session = this.sessions.get(sessionId);
    if (!session) {
      console.error(`[server] session '${sessionId}' not found`);
    }
    return session;
  }
}
