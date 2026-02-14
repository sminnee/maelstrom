#!/usr/bin/env node

/**
 * CLI mode for agent-cli.
 *
 * Runs a Claude Code session in the terminal with interactive permission
 * prompts. The socket server runs alongside so the desktop app can
 * optionally connect to observe and send follow-up prompts.
 */

import * as readline from "node:readline";
import { AgentServer, DEFAULT_SOCKET_PATH } from "./server.js";
import { AgentSession } from "./session.js";
import type {
  OutboundMessage,
  PermissionRequestEvent,
  QuestionEvent,
} from "./protocol.js";

const CLI_SESSION_ID = "cli";

export async function runCli(prompt: string, cwd: string): Promise<void> {
  const socketPath = process.env.MAELSTROM_SOCKET ?? DEFAULT_SOCKET_PATH;

  // Start the socket server so the desktop app can connect
  const server = new AgentServer(socketPath);
  await server.start();

  // Shared readline for both permission prompts and follow-up input
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  // Create the session with dual-output: terminal + socket broadcast
  const session = new AgentSession(CLI_SESSION_ID, (msg) => {
    renderToTerminal(msg);
    server.broadcast(msg);

    // Handle interactive permission/question prompts
    if (msg.type === "permission_request") {
      promptPermission(rl, msg, session);
    } else if (msg.type === "question") {
      promptQuestion(rl, msg, session);
    }
  });

  // Register with server so socket clients can send_prompt, interrupt, etc.
  server.registerSession(CLI_SESSION_ID, session);

  const sessionStarted = prompt.length > 0;

  if (sessionStarted) {
    // Start the session with the provided prompt
    await session.start({
      type: "start_session",
      sessionId: CLI_SESSION_ID,
      cwd,
      prompt,
    });
  }

  // Enter interactive loop (handles first prompt if session not yet started)
  await followUpLoop(session, server, cwd, sessionStarted, rl);
}

async function followUpLoop(
  session: AgentSession,
  server: AgentServer,
  cwd: string,
  sessionStarted: boolean,
  rl: readline.Interface,
): Promise<void> {
  const askForInput = (): Promise<string | null> => {
    return new Promise((resolve) => {
      rl.question("> ", (answer) => {
        resolve(answer.trim() || null);
      });
      rl.once("close", () => resolve(null));
    });
  };

  let started = sessionStarted;

  while (true) {
    const input = await askForInput();
    if (input === null) {
      // EOF or Ctrl+D
      break;
    }

    if (!started) {
      // First input starts the session
      await session.start({
        type: "start_session",
        sessionId: CLI_SESSION_ID,
        cwd,
        prompt: input,
      });
      started = true;
    } else {
      await session.sendPrompt(input);
    }
  }

  rl.close();
  await session.close();
  await server.stop();
}

// ─── Permission & Question Prompts ─────────────────────────

function promptPermission(
  rl: readline.Interface,
  msg: PermissionRequestEvent,
  session: AgentSession,
): void {
  const summary = summarizeToolInput(msg.toolName, msg.toolInput);
  rl.question(
    `  Allow ${msg.toolName}${summary ? `: ${summary}` : ""}? (y/n) `,
    (answer) => {
      const allowed = answer.trim().toLowerCase() !== "n";
      session.resolvePermission(msg.requestId, allowed ? "allow" : "deny");
    },
  );
}

function promptQuestion(
  rl: readline.Interface,
  msg: QuestionEvent,
  session: AgentSession,
): void {
  const answers: Record<string, string> = {};
  let i = 0;

  const askNext = () => {
    if (i >= msg.questions.length) {
      session.resolveQuestion(msg.requestId, answers);
      return;
    }

    const q = msg.questions[i];
    let text = `\n  ${q.question}\n`;
    q.options.forEach((opt, idx) => {
      text += `    ${idx + 1}. ${opt.label} — ${opt.description}\n`;
    });
    text += `  Choice (1-${q.options.length}): `;

    rl.question(text, (answer) => {
      const idx = parseInt(answer.trim()) - 1;
      if (idx >= 0 && idx < q.options.length) {
        answers[q.header] = q.options[idx].label;
      } else {
        // Treat raw text as custom input
        answers[q.header] = answer.trim();
      }
      i++;
      askNext();
    });
  };

  askNext();
}

// ─── Terminal Rendering ──────────────────────────────────────

let isStreamingText = false;

function renderToTerminal(msg: OutboundMessage): void {
  switch (msg.type) {
    case "system":
      finishStreaming();
      console.log(`[system] Session started (model: ${msg.model ?? "unknown"})`);
      break;

    case "assistant_text":
      finishStreaming();
      process.stdout.write(`\n${msg.text}\n`);
      break;

    case "stream_delta":
      handleStreamDelta(msg.delta);
      break;

    case "tool_use":
      finishStreaming();
      console.log(`\n[tool] ${msg.toolName}: ${summarizeToolInput(msg.toolName, msg.toolInput)}`);
      break;

    case "tool_result":
      // Tool results can be verbose; show a brief summary
      if (msg.isError) {
        console.error(`[tool] Error: ${truncate(String(msg.output), 200)}`);
      }
      break;

    case "result":
      finishStreaming();
      const cost = msg.totalCostUsd != null ? `$${msg.totalCostUsd.toFixed(4)}` : "?";
      const duration = msg.durationMs != null ? `${(msg.durationMs / 1000).toFixed(1)}s` : "?";
      console.log(`\n[result] Done (${msg.numTurns} turns, ${cost}, ${duration})`);
      break;

    case "error":
      finishStreaming();
      console.error(`[error] ${msg.error}`);
      break;

    case "session_status":
      // Only log non-routine status changes
      if (msg.status === "closed") {
        finishStreaming();
        console.log("[session] Closed");
      }
      break;

    case "permission_request":
    case "question":
      // Handled by promptPermission/promptQuestion via the message callback
      finishStreaming();
      break;
  }
}

function handleStreamDelta(delta: unknown): void {
  if (typeof delta !== "object" || delta === null) return;
  const d = delta as Record<string, unknown>;

  // Handle content_block_delta events with text deltas
  if (d.type === "content_block_delta" && d.delta && typeof d.delta === "object") {
    const inner = d.delta as Record<string, unknown>;
    if (inner.type === "text_delta" && typeof inner.text === "string") {
      if (!isStreamingText) {
        process.stdout.write("\n");
        isStreamingText = true;
      }
      process.stdout.write(inner.text);
    }
  }
}

function finishStreaming(): void {
  if (isStreamingText) {
    process.stdout.write("\n");
    isStreamingText = false;
  }
}

function summarizeToolInput(toolName: string, input: unknown): string {
  if (typeof input !== "object" || input === null) return "";
  const obj = input as Record<string, unknown>;

  switch (toolName) {
    case "Read":
      return String(obj.file_path ?? "");
    case "Write":
      return String(obj.file_path ?? "");
    case "Edit":
      return String(obj.file_path ?? "");
    case "Bash":
      return truncate(String(obj.command ?? ""), 120);
    case "Glob":
      return String(obj.pattern ?? "");
    case "Grep":
      return `/${obj.pattern ?? ""}/ ${obj.path ?? ""}`;
    case "WebFetch":
      return String(obj.url ?? "");
    default:
      return truncate(JSON.stringify(input), 120);
  }
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 3) + "..." : s;
}
