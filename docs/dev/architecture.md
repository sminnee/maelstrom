# Communication Architecture

This document describes the communication architecture between the Maelstrom
desktop app and the agent-cli bridge that manages Claude Code sessions.

## System Overview

```
┌─────────────────────────┐
│   Desktop App           │
│   (Tauri / React)       │
│                         │
│  ┌───────────────────┐  │
│  │ React Frontend    │  │
│  │ (Session UI)      │  │
│  └────────┬──────────┘  │
│           │ Tauri IPC   │
│  ┌────────┴──────────┐  │
│  │ Rust Backend      │  │
│  │ (Socket Client)   │  │
│  └────────┬──────────┘  │
└───────────┼─────────────┘
            │
            │ Unix Domain Socket
            │ /tmp/maelstrom/agent.sock
            │ NDJSON protocol
            │
┌───────────┼─────────────┐
│  agent-cli│              │
│  ┌────────┴──────────┐  │
│  │ AgentServer       │  │
│  │ (Socket Server)   │  │
│  └────────┬──────────┘  │
│           │              │
│  ┌────────┴──────────┐  │
│  │ AgentSession(s)   │  │
│  │ (SDK Wrapper)     │  │
│  └────────┬──────────┘  │
│           │              │
│  ┌────────┴──────────┐  │
│  │ Claude Agent SDK  │  │
│  │ (@anthropic-ai/   │  │
│  │  claude-agent-sdk)│  │
│  └────────┬──────────┘  │
└───────────┼─────────────┘
            │ Spawns subprocess
            │
┌───────────┼─────────────┐
│  Claude Code CLI        │
│  (handles API calls,    │
│   tool execution,       │
│   file operations)      │
└─────────────────────────┘
```

## IPC: Unix Domain Sockets

Communication between the desktop app and agent-cli uses a **Unix domain
socket** at `/tmp/maelstrom/agent.sock`.

### Why Unix domain sockets?

- **Fast**: No TCP/IP stack overhead; kernel copies data directly between
  process buffers
- **Bidirectional**: Full duplex communication over a single connection
- **Secure**: File system permissions control access; `SO_PEERCRED` allows
  verifying the peer's user/group identity
- **No port conflicts**: Uses a file path instead of a port number

### Wire Protocol: NDJSON

Messages are **newline-delimited JSON** (NDJSON). Each message is a single
JSON object on one line, terminated by `\n`.

```
{"type":"start_session","sessionId":"s1","cwd":"/project","prompt":"Fix the bug"}\n
{"type":"session_status","sessionId":"s1","status":"started"}\n
{"type":"assistant_text","sessionId":"s1","uuid":"...","text":"I'll look at..."}\n
```

**Why NDJSON?**
- Simple to implement (JSON.parse per line)
- Human-readable for debugging (`socat` or `nc` can interact with it)
- Self-framing (newline delimiter)
- Well-supported across languages (Rust, TypeScript, Python)

## Session Multiplexing

A single socket connection supports **multiple concurrent Claude sessions**.
Every message includes a `sessionId` field chosen by the desktop app. The
agent-cli routes messages to the correct `AgentSession` instance by this ID.

```
Desktop App                        agent-cli
    │                                  │
    │──start_session(sessionId="s1")──>│  → creates AgentSession "s1"
    │──start_session(sessionId="s2")──>│  → creates AgentSession "s2"
    │                                  │
    │<──assistant_text(sessionId="s1")─│  ← from session "s1"
    │<──assistant_text(sessionId="s2")─│  ← from session "s2"
```

## Message Types

### Inbound (Desktop App → agent-cli)

| Type | Purpose |
|------|---------|
| `start_session` | Create a new Claude session with a prompt and configuration |
| `send_prompt` | Send a follow-up prompt to an existing session |
| `resume_session` | Resume a previous session by its Claude session ID |
| `permission_response` | Respond to a tool permission request (allow/deny) |
| `question_response` | Respond to an AskUserQuestion from Claude |
| `interrupt` | Stop the currently running query |
| `close_session` | End and clean up a session |

### Outbound (agent-cli → Desktop App)

| Type | Purpose |
|------|---------|
| `system` | Session initialization metadata (model, tools, session ID) |
| `assistant_text` | Claude's text output (extracted from content blocks) |
| `tool_use` | Claude wants to use a tool (informational) |
| `tool_result` | Result of a tool execution |
| `permission_request` | Asks the desktop app for permission to use a tool |
| `question` | Forwards an AskUserQuestion to the desktop app |
| `stream_delta` | Raw streaming event for real-time text display |
| `result` | Session completed (cost, duration, turn count) |
| `error` | Error occurred |
| `session_status` | Lifecycle event (started, idle, processing, closed) |

See [protocol.ts](../../agent-cli/src/protocol.ts) for full TypeScript type
definitions.

## Key Mechanism: Permission Bridging

The Claude Agent SDK calls a `canUseTool` callback whenever Claude wants to
execute a tool. The agent-cli uses this to bridge permission decisions to the
desktop app:

```
Desktop App                   agent-cli                     Claude SDK
    │                             │                              │
    │                             │<──canUseTool(Bash, input)────│
    │                             │   SDK is blocked waiting     │
    │<──permission_request────────│                              │
    │   {requestId, toolName,     │                              │
    │    toolInput}               │                              │
    │                             │                              │
    │   (user reviews in UI)      │                              │
    │                             │                              │
    │──permission_response───────>│                              │
    │   {requestId,               │                              │
    │    behavior: "allow"}       │                              │
    │                             │──PermissionResult.allow─────>│
    │                             │   SDK continues execution    │
```

The `requestId` field correlates requests with responses. The SDK's Promise
remains pending until the desktop app responds, providing natural
backpressure without polling.

### AskUserQuestion Flow

When Claude uses the `AskUserQuestion` tool, the same mechanism applies but
with a different message type:

```
Desktop App                   agent-cli                     Claude SDK
    │                             │                              │
    │                             │<──canUseTool(AskUserQuestion)│
    │<──question──────────────────│                              │
    │   {requestId, questions[]}  │                              │
    │                             │                              │
    │──question_response─────────>│                              │
    │   {requestId, answers}      │                              │
    │                             │──allow({answers})───────────>│
    │                             │   SDK continues              │
```

## Multi-turn Conversations

Follow-up prompts use **session resume**:

1. The first `query()` call emits a `system` message containing the SDK's
   `claudeSessionId`
2. The agent-cli captures this ID
3. When a `send_prompt` arrives, a new `query()` call is made with
   `resume: claudeSessionId`
4. Claude picks up with full conversation context

```
Desktop App                   agent-cli                     Claude SDK
    │                             │                              │
    │──start_session(prompt)─────>│──query(prompt)──────────────>│
    │<──system(claudeSessionId)───│<──SDKSystemMessage───────────│
    │<──assistant_text────────────│<──SDKAssistantMessage────────│
    │<──result────────────────────│<──SDKResultMessage───────────│
    │<──session_status(idle)──────│                              │
    │                             │                              │
    │──send_prompt(follow-up)────>│──query(resume: sid)─────────>│
    │<──assistant_text────────────│<──SDKAssistantMessage────────│
    │<──result────────────────────│<──SDKResultMessage───────────│
```

## Startup Sequence

1. Desktop app spawns agent-cli as a child process
2. agent-cli creates the Unix socket server at `/tmp/maelstrom/agent.sock`
3. agent-cli writes `{"status":"ready","socketPath":"..."}` to stdout
4. Desktop app reads the ready signal and connects to the socket
5. Desktop app sends `start_session` messages to begin work

## Error Handling

- **Disconnection**: If the desktop app disconnects, pending permission
  requests are auto-denied with "Session closed" and sessions are cleaned up
- **SDK errors**: Caught and forwarded as `error` events to the desktop app
- **Invalid messages**: Rejected with an `error` event; the connection stays
  open
- **Socket cleanup**: On startup, any stale socket file is removed to prevent
  "address in use" errors

## File Layout

```
agent-cli/
  src/
    index.ts        Entry point, signal handling, starts server
    protocol.ts     TypeScript types for all NDJSON messages
    transport.ts    NDJSON framing over net.Socket
    session.ts      Wraps Claude Agent SDK, bridges permissions
    server.ts       Unix socket server, connection routing
```
