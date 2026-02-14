/**
 * AgentSession wraps a single Claude Agent SDK query, bridging
 * SDK messages and permission requests to the NDJSON protocol.
 *
 * Key mechanisms:
 * - canUseTool callback: intercepts permission requests, sends them
 *   to the desktop app via NDJSON, and blocks until a response arrives
 * - Message forwarding: converts SDK messages to protocol events
 * - Multi-turn: uses session resume for follow-up prompts
 */

import { query } from "@anthropic-ai/claude-agent-sdk";
import type {
  SDKMessage,
  SDKAssistantMessage,
  SDKResultMessage,
  SDKSystemMessage,
  SDKPartialAssistantMessage,
  SDKUserMessage,
  Options,
  PermissionResult,
  PermissionUpdate,
  Query,
} from "@anthropic-ai/claude-agent-sdk";
import type { OutboundMessage, StartSessionMessage } from "./protocol.js";

interface PendingRequest {
  resolve: (result: PermissionResult) => void;
  toolInput: Record<string, unknown>;
}

export class AgentSession {
  private activeQuery: Query | null = null;
  private claudeSessionId: string | null = null;
  private pendingRequests = new Map<string, PendingRequest>();
  private requestCounter = 0;
  private lastCwd: string;

  constructor(
    readonly sessionId: string,
    private sendToClient: (msg: OutboundMessage) => void,
  ) {
    this.lastCwd = process.cwd();
  }

  /**
   * Start a new Claude session with the given configuration.
   */
  async start(config: StartSessionMessage): Promise<void> {
    this.lastCwd = config.cwd;

    this.sendToClient({
      sessionId: this.sessionId,
      type: "session_status",
      status: "started",
    });

    const options: Options = {
      cwd: config.cwd,
      model: config.model,
      permissionMode: config.permissionMode,
      allowedTools: config.allowedTools,
      settingSources: config.settingSources ?? ["project"],
      systemPrompt: config.systemPrompt ?? {
        type: "preset",
        preset: "claude_code",
      },
      includePartialMessages: true,
      canUseTool: (toolName, input, opts) =>
        this.handleCanUseTool(toolName, input, opts),
    };

    await this.runQuery(config.prompt, options);
  }

  /**
   * Send a follow-up prompt to the existing session.
   * Uses session resume to maintain conversation context.
   */
  async sendPrompt(prompt: string): Promise<void> {
    if (!this.claudeSessionId) {
      this.sendToClient({
        sessionId: this.sessionId,
        type: "error",
        error: "No active session to send prompt to",
      });
      return;
    }

    const options: Options = {
      resume: this.claudeSessionId,
      cwd: this.lastCwd,
      includePartialMessages: true,
      canUseTool: (toolName, input, opts) =>
        this.handleCanUseTool(toolName, input, opts),
    };

    await this.runQuery(prompt, options);
  }

  /**
   * Resume a previous session by its Claude session ID.
   */
  async resume(claudeSessionId: string, prompt: string, cwd: string): Promise<void> {
    this.lastCwd = cwd;

    const options: Options = {
      resume: claudeSessionId,
      cwd,
      includePartialMessages: true,
      canUseTool: (toolName, input, opts) =>
        this.handleCanUseTool(toolName, input, opts),
    };

    await this.runQuery(prompt, options);
  }

  /**
   * Interrupt the currently running query.
   */
  async interrupt(): Promise<void> {
    if (this.activeQuery) {
      await this.activeQuery.interrupt();
    }
  }

  /**
   * Close the session, rejecting any pending requests.
   */
  async close(): Promise<void> {
    if (this.activeQuery) {
      this.activeQuery.close();
      this.activeQuery = null;
    }

    for (const [, pending] of this.pendingRequests) {
      pending.resolve({ behavior: "deny", message: "Session closed" });
    }
    this.pendingRequests.clear();

    this.sendToClient({
      sessionId: this.sessionId,
      type: "session_status",
      status: "closed",
    });
  }

  /**
   * Resolve a pending permission request from the desktop app.
   */
  resolvePermission(
    requestId: string,
    behavior: "allow" | "deny",
    updatedInput?: Record<string, unknown>,
    message?: string,
  ): void {
    const pending = this.pendingRequests.get(requestId);
    if (!pending) return;
    this.pendingRequests.delete(requestId);

    if (behavior === "allow") {
      pending.resolve({
        behavior: "allow",
        updatedInput: updatedInput ?? pending.toolInput,
      });
    } else {
      pending.resolve({
        behavior: "deny",
        message: message ?? "Denied by user",
      });
    }
  }

  /**
   * Resolve a pending AskUserQuestion from the desktop app.
   */
  resolveQuestion(requestId: string, answers: Record<string, string>): void {
    const pending = this.pendingRequests.get(requestId);
    if (!pending) return;
    this.pendingRequests.delete(requestId);

    pending.resolve({
      behavior: "allow",
      updatedInput: { ...pending.toolInput, answers },
    });
  }

  // ─── Private ────────────────────────────────────────────────

  private async runQuery(prompt: string, options: Options): Promise<void> {
    this.sendToClient({
      sessionId: this.sessionId,
      type: "session_status",
      status: "processing",
    });

    try {
      this.activeQuery = query({ prompt, options });

      for await (const msg of this.activeQuery) {
        this.forwardMessage(msg);
      }
    } catch (err) {
      this.sendToClient({
        sessionId: this.sessionId,
        type: "error",
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      this.activeQuery = null;
      this.sendToClient({
        sessionId: this.sessionId,
        type: "session_status",
        status: "idle",
      });
    }
  }

  private forwardMessage(msg: SDKMessage): void {
    switch (msg.type) {
      case "system":
        this.handleSystemMessage(msg as SDKSystemMessage);
        break;
      case "assistant":
        this.handleAssistantMessage(msg as SDKAssistantMessage);
        break;
      case "result":
        this.handleResultMessage(msg as SDKResultMessage);
        break;
      case "stream_event":
        this.handleStreamEvent(msg as SDKPartialAssistantMessage);
        break;
      case "user":
        // User messages are echoes of our own input; skip
        break;
      default:
        // Other message types (status, hook events, etc.) - skip for now
        break;
    }
  }

  private handleSystemMessage(msg: SDKSystemMessage): void {
    this.claudeSessionId = msg.session_id;
    this.sendToClient({
      sessionId: this.sessionId,
      type: "system",
      subtype: msg.subtype,
      claudeSessionId: msg.session_id,
      tools: msg.tools,
      model: msg.model,
      cwd: msg.cwd,
    });
  }

  private handleAssistantMessage(msg: SDKAssistantMessage): void {
    const content = msg.message.content;
    if (!Array.isArray(content)) return;

    // Extract and forward text blocks
    const textParts: string[] = [];
    for (const block of content) {
      if (typeof block === "object" && "type" in block) {
        if (block.type === "text" && "text" in block) {
          textParts.push(block.text as string);
        } else if (block.type === "tool_use" && "id" in block && "name" in block) {
          this.sendToClient({
            sessionId: this.sessionId,
            type: "tool_use",
            uuid: msg.uuid,
            toolName: block.name as string,
            toolInput: "input" in block ? block.input : {},
            toolUseId: block.id as string,
          });
        } else if (block.type === "tool_result" && "tool_use_id" in block) {
          this.sendToClient({
            sessionId: this.sessionId,
            type: "tool_result",
            uuid: msg.uuid,
            toolUseId: block.tool_use_id as string,
            output: "content" in block ? block.content : null,
            isError: "is_error" in block ? (block.is_error as boolean) : false,
          });
        }
      }
    }

    if (textParts.length > 0) {
      this.sendToClient({
        sessionId: this.sessionId,
        type: "assistant_text",
        uuid: msg.uuid,
        text: textParts.join(""),
      });
    }
  }

  private handleResultMessage(msg: SDKResultMessage): void {
    this.sendToClient({
      sessionId: this.sessionId,
      type: "result",
      subtype: msg.subtype,
      isError: msg.is_error,
      result: "result" in msg ? (msg as { result: string }).result : undefined,
      totalCostUsd: msg.total_cost_usd,
      durationMs: msg.duration_ms,
      numTurns: msg.num_turns,
      errors: "errors" in msg ? (msg as { errors: string[] }).errors : undefined,
    });
  }

  private handleStreamEvent(msg: SDKPartialAssistantMessage): void {
    this.sendToClient({
      sessionId: this.sessionId,
      type: "stream_delta",
      uuid: msg.uuid,
      delta: msg.event,
    });
  }

  /**
   * Called by the SDK whenever it wants to use a tool.
   * For AskUserQuestion, forwards the question to the desktop app.
   * For all other tools, forwards a permission request.
   * Returns a Promise that resolves when the desktop app responds.
   */
  private handleCanUseTool(
    toolName: string,
    input: Record<string, unknown>,
    opts: {
      signal: AbortSignal;
      suggestions?: PermissionUpdate[];
      toolUseID: string;
    },
  ): Promise<PermissionResult> {
    const requestId = `req_${++this.requestCounter}`;

    if (toolName === "AskUserQuestion") {
      this.sendToClient({
        sessionId: this.sessionId,
        type: "question",
        requestId,
        questions: input.questions as QuestionData[],
      });
    } else {
      this.sendToClient({
        sessionId: this.sessionId,
        type: "permission_request",
        requestId,
        toolName,
        toolInput: input,
        toolUseId: opts.toolUseID,
        suggestions: opts.suggestions,
      });
    }

    return new Promise<PermissionResult>((resolve) => {
      this.pendingRequests.set(requestId, { resolve, toolInput: input });
    });
  }
}

// Internal type for question data forwarding
type QuestionData = {
  question: string;
  header: string;
  options: Array<{ label: string; description: string }>;
  multiSelect: boolean;
};
