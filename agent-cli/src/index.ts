#!/usr/bin/env node

/**
 * agent-cli entry point.
 *
 * Default: CLI mode - runs a Claude Code session in the terminal
 * with the socket server alongside for desktop app integration.
 *
 * --server: Server-only mode - just the socket server, no terminal I/O.
 */

import { AgentServer, DEFAULT_SOCKET_PATH } from "./server.js";
import { runCli } from "./cli.js";

const args = process.argv.slice(2);

if (args.includes("--server")) {
  // Server-only mode (for desktop-app-driven use)
  const socketPath =
    args.find((a) => a.startsWith("--socket="))?.split("=")[1] ??
    DEFAULT_SOCKET_PATH;

  const server = new AgentServer(socketPath);

  async function shutdown() {
    await server.stop();
    process.exit(0);
  }

  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);

  server.start().then(() => {
    console.log(JSON.stringify({ status: "ready", socketPath }));
  });
} else {
  // CLI mode (default)
  const prompt = args.filter((a) => !a.startsWith("--")).join(" ");
  const cwd = process.cwd();

  // Handle Ctrl+C: first press interrupts, process handles exit on second
  let interrupted = false;
  process.on("SIGINT", () => {
    if (interrupted) {
      process.exit(0);
    }
    interrupted = true;
    setTimeout(() => {
      interrupted = false;
    }, 2000);
  });

  runCli(prompt, cwd).catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
