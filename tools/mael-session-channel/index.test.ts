// Focused tests for the session-channel's registry key derivation.
//
// The registry's primary key must be the deterministic session id. Since the
// Claude harness does not export CLAUDE_SESSION_ID to channel subprocesses,
// `mael task run` exports it as MAEL_SESSION_ID; these tests pin that the
// channel prefers it, falls back to CLAUDE_SESSION_ID, and only then to the
// pid-based key. Run with `bun test`.

import { expect, test, describe } from "bun:test";
import { sessionId, sessionKey } from "./index.ts";

describe("sessionId", () => {
  test("prefers MAEL_SESSION_ID", () => {
    expect(
      sessionId({ MAEL_SESSION_ID: "det-id", CLAUDE_SESSION_ID: "claude-id" }),
    ).toBe("det-id");
  });

  test("falls back to CLAUDE_SESSION_ID", () => {
    expect(sessionId({ CLAUDE_SESSION_ID: "claude-id" })).toBe("claude-id");
  });

  test("is null when neither is set", () => {
    expect(sessionId({})).toBeNull();
  });

  test("ignores empty strings", () => {
    expect(sessionId({ MAEL_SESSION_ID: "", CLAUDE_SESSION_ID: "" })).toBeNull();
  });
});

describe("sessionKey", () => {
  test("is the deterministic id when known", () => {
    expect(sessionKey({ MAEL_SESSION_ID: "det-id" }, 999)).toBe("det-id");
  });

  test("falls back to a pid-based key with no id", () => {
    expect(sessionKey({}, 4242)).toBe("claude-4242");
  });
});
