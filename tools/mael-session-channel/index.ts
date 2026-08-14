// Mael session-tracking MCP channel.
//
// Spawned by Claude Code (via the `mael session-channel` launcher) for every
// session. Writes ~/.maelstrom/sessions/<session-key>.json with cwd, pid,
// model, and a liveness port on startup, and deletes the file on shutdown.
//
// The HTTP listener on a 127.0.0.1:<random> port exists only so the
// `mael session list` GC pass can probe whether the channel is still alive.

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { createServer } from "node:http";
import { mkdirSync, writeFileSync, unlinkSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const SESSIONS_DIR = join(homedir(), ".maelstrom", "sessions");

type Env = Record<string, string | undefined>;

// The session id this registry file is keyed by, if known.
//
// There are two different session ids, answering two different questions:
//
//   - The id `mael task run` derives from the task. It exists before the session
//     is launched and never changes, so it is what links a registry file back to
//     its task. It arrives as MAEL_TASK_SESSION_ID.
//   - The id of the conversation running now. Claude Code exports this as
//     CLAUDE_CODE_SESSION_ID. A `/clear` starts a new conversation and moves it,
//     so it cannot key a task.
//
// The derived id therefore wins. MAEL_SESSION_ID is the old name for it, kept so
// a session launched before the rename keeps the same key for its whole life.
// CLAUDE_CODE_SESSION_ID is the last resort, for a session `mael` did not launch.
// Exported (with an injectable `env`) so it is unit testable without spawning a
// real channel.
export function sessionId(env: Env = process.env): string | null {
  const candidates = [
    env.MAEL_TASK_SESSION_ID,
    env.MAEL_SESSION_ID,
    env.CLAUDE_CODE_SESSION_ID,
  ];
  for (const id of candidates) {
    if (id && id.length > 0) {
      return id;
    }
  }
  return null;
}

export function sessionKey(env: Env = process.env, pid: number = process.pid): string {
  const id = sessionId(env);
  if (id) {
    return id;
  }
  return `claude-${pid}`;
}

function nowIso(): string {
  return new Date().toISOString();
}

async function startLivenessListener(): Promise<number> {
  return new Promise((resolve) => {
    const srv = createServer((_req, res) => {
      res.writeHead(200, { "Content-Type": "text/plain" });
      res.end("ok");
    });
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address();
      if (addr && typeof addr === "object") {
        resolve(addr.port);
      } else {
        resolve(0);
      }
    });
  });
}

function writeRegistry(file: string, data: object): void {
  mkdirSync(SESSIONS_DIR, { recursive: true });
  // Atomic-ish: write to tmp then rename.
  const tmp = `${file}.tmp`;
  writeFileSync(tmp, JSON.stringify(data, null, 2));
  // node:fs renameSync is atomic on posix.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { renameSync } = require("node:fs");
  renameSync(tmp, file);
}

function deleteRegistry(file: string): void {
  try {
    if (existsSync(file)) {
      unlinkSync(file);
    }
  } catch {
    // best-effort
  }
}

async function main() {
  const key = sessionKey();
  const file = join(SESSIONS_DIR, `${key}.json`);
  const port = await startLivenessListener();
  const startedAt = nowIso();

  const data = {
    session_key: key,
    session_id: sessionId(),
    cwd: process.cwd(),
    pid: process.pid,
    model: process.env.CLAUDE_MODEL || null,
    // The launching `mael task run` exports MAEL_TASK_ID; recording it here
    // lets `mael task reconcile` map a live session back to its task without a
    // live env var (the deterministic session_id is the primary key, this is a
    // human-readable confirmation).
    mael_task_id: process.env.MAEL_TASK_ID || null,
    state: "idle",
    started_at: startedAt,
    updated_at: startedAt,
    channel_port: port,
  };

  writeRegistry(file, data);

  const cleanup = () => {
    deleteRegistry(file);
  };

  process.on("SIGTERM", () => {
    cleanup();
    process.exit(0);
  });
  process.on("SIGINT", () => {
    cleanup();
    process.exit(0);
  });
  process.on("exit", cleanup);

  // Minimal MCP server — declares a `claude/channel` capability so Claude
  // Code recognises it as a channel even though we don't expose any tools
  // or messages for v1.
  const server = new Server(
    {
      name: "mael-session",
      version: "0.1.0",
    },
    {
      capabilities: {
        "claude/channel": {},
      },
    },
  );

  const transport = new StdioServerTransport();
  transport.onclose = () => {
    cleanup();
    process.exit(0);
  };
  await server.connect(transport);
}

// Only spawn the channel when run as the entrypoint, so importing this module
// in a test (to exercise sessionId/sessionKey) does not start a real server.
if (import.meta.main) {
  main().catch((err) => {
    // Best-effort: log and exit cleanly so Claude Code surfaces the error.
    // The registry file may or may not have been written; the GC will clear
    // it on the next `mael session list`.
    console.error("[mael-session-channel] fatal:", err);
    process.exit(1);
  });
}
