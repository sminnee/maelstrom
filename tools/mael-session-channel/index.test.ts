// Focused tests for the session-channel's registry key derivation.
//
// The registry's primary key must be the task's derived session id, which never
// moves. `mael task run` exports it as MAEL_TASK_SESSION_ID. These tests pin the
// precedence: the derived id first, then the old MAEL_SESSION_ID name for a
// session launched before the rename, then the harness's live
// CLAUDE_CODE_SESSION_ID, and only then the pid-based key. Run with `bun test`.

import { expect, test, describe } from "bun:test";
import { sessionId, sessionKey } from "./index.ts";

describe("sessionId", () => {
  test("prefers MAEL_TASK_SESSION_ID", () => {
    expect(
      sessionId({
        MAEL_TASK_SESSION_ID: "det-id",
        MAEL_SESSION_ID: "old-id",
        CLAUDE_CODE_SESSION_ID: "live-id",
      }),
    ).toBe("det-id");
  });

  test("still accepts the old MAEL_SESSION_ID name", () => {
    // A session launched before the rename carries only the old name; its
    // registry file must keep the same key for its whole life.
    expect(
      sessionId({ MAEL_SESSION_ID: "old-id", CLAUDE_CODE_SESSION_ID: "live-id" }),
    ).toBe("old-id");
  });

  test("falls back to the harness's live CLAUDE_CODE_SESSION_ID", () => {
    expect(sessionId({ CLAUDE_CODE_SESSION_ID: "live-id" })).toBe("live-id");
  });

  test("is null when none is set", () => {
    expect(sessionId({})).toBeNull();
  });

  test("ignores empty strings", () => {
    expect(
      sessionId({
        MAEL_TASK_SESSION_ID: "",
        MAEL_SESSION_ID: "",
        CLAUDE_CODE_SESSION_ID: "",
      }),
    ).toBeNull();
  });
});

describe("sessionKey", () => {
  test("is the derived id when known", () => {
    expect(sessionKey({ MAEL_TASK_SESSION_ID: "det-id" }, 999)).toBe("det-id");
  });

  test("falls back to a pid-based key with no id", () => {
    expect(sessionKey({}, 4242)).toBe("claude-4242");
  });
});
