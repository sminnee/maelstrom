/**
 * NDJSON protocol message types for communication between
 * the desktop app (Tauri/React) and the agent-cli.
 *
 * Every message is a single JSON object terminated by \n.
 * All messages carry a `sessionId` for multiplexing multiple
 * concurrent Claude sessions over a single socket connection.
 */

// ─── Inbound: Desktop App → CLI ───────────────────────────────

interface InboundBase {
  sessionId: string;
}

export interface StartSessionMessage extends InboundBase {
  type: "start_session";
  cwd: string;
  prompt: string;
  systemPrompt?: string;
  model?: string;
  permissionMode?: "default" | "acceptEdits" | "bypassPermissions" | "plan";
  allowedTools?: string[];
  settingSources?: ("user" | "project" | "local")[];
}

export interface SendPromptMessage extends InboundBase {
  type: "send_prompt";
  prompt: string;
}

export interface ResumeSessionMessage extends InboundBase {
  type: "resume_session";
  claudeSessionId: string;
  prompt: string;
  cwd: string;
}

export interface PermissionResponseMessage extends InboundBase {
  type: "permission_response";
  requestId: string;
  behavior: "allow" | "deny";
  updatedInput?: Record<string, unknown>;
  message?: string;
}

export interface QuestionResponseMessage extends InboundBase {
  type: "question_response";
  requestId: string;
  answers: Record<string, string>;
}

export interface InterruptMessage extends InboundBase {
  type: "interrupt";
}

export interface CloseSessionMessage extends InboundBase {
  type: "close_session";
}

export type InboundMessage =
  | StartSessionMessage
  | SendPromptMessage
  | ResumeSessionMessage
  | PermissionResponseMessage
  | QuestionResponseMessage
  | InterruptMessage
  | CloseSessionMessage;

// ─── Outbound: CLI → Desktop App ──────────────────────────────

interface OutboundBase {
  sessionId: string;
}

export interface AssistantTextEvent extends OutboundBase {
  type: "assistant_text";
  uuid: string;
  text: string;
}

export interface ToolUseEvent extends OutboundBase {
  type: "tool_use";
  uuid: string;
  toolName: string;
  toolInput: unknown;
  toolUseId: string;
}

export interface ToolResultEvent extends OutboundBase {
  type: "tool_result";
  uuid: string;
  toolUseId: string;
  output: unknown;
  isError?: boolean;
}

export interface PermissionRequestEvent extends OutboundBase {
  type: "permission_request";
  requestId: string;
  toolName: string;
  toolInput: unknown;
  toolUseId: string;
  suggestions?: unknown[];
}

export interface QuestionEvent extends OutboundBase {
  type: "question";
  requestId: string;
  questions: Array<{
    question: string;
    header: string;
    options: Array<{ label: string; description: string }>;
    multiSelect: boolean;
  }>;
}

export interface SystemEvent extends OutboundBase {
  type: "system";
  subtype: string;
  claudeSessionId: string;
  tools?: string[];
  model?: string;
  cwd?: string;
}

export interface ResultEvent extends OutboundBase {
  type: "result";
  subtype: string;
  isError: boolean;
  result?: string;
  totalCostUsd: number;
  durationMs: number;
  numTurns: number;
  errors?: string[];
}

export interface StreamDeltaEvent extends OutboundBase {
  type: "stream_delta";
  uuid: string;
  delta: unknown;
}

export interface ErrorEvent extends OutboundBase {
  type: "error";
  error: string;
  code?: string;
}

export interface SessionStatusEvent extends OutboundBase {
  type: "session_status";
  status: "started" | "idle" | "processing" | "closed" | "error";
}

export type OutboundMessage =
  | AssistantTextEvent
  | ToolUseEvent
  | ToolResultEvent
  | PermissionRequestEvent
  | QuestionEvent
  | SystemEvent
  | ResultEvent
  | StreamDeltaEvent
  | ErrorEvent
  | SessionStatusEvent;
