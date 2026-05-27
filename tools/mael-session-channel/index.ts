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

function sessionKey(): string {
  const envId = process.env.CLAUDE_SESSION_ID;
  if (envId && envId.length > 0) {
    return envId;
  }
  return `claude-${process.pid}`;
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
    session_id: process.env.CLAUDE_SESSION_ID || null,
    cwd: process.cwd(),
    pid: process.pid,
    model: process.env.CLAUDE_MODEL || null,
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

main().catch((err) => {
  // Best-effort: log and exit cleanly so Claude Code surfaces the error.
  // The registry file may or may not have been written; the GC will clear
  // it on the next `mael session list`.
  console.error("[mael-session-channel] fatal:", err);
  process.exit(1);
});
