# Live feedback

**Live feedback** connects a client in a dev server to the agents of its worktree. The client
declares **Live feedback params**, and the orchestrator draws a switch for each one in the
**Dev env tab**. The client also sends **Feedback** to the agents. This page tells a project how to
write a client. The **Jig** in `vite-plugin-mael-jig/src/plugin.ts` is the worked
example.

The orchestrator names nothing of a client. The world, the API and the UI carry params by name,
and the client gives each one its label.

## The URL

`mael env start` sets `MAEL_FEEDBACK_URL` in each service's environment. The URL names the
worktree and the service, so a client dials it as it is and sends no identity of its own:

```
MAEL_FEEDBACK_URL=wss://desk.tailnet.ts.net:2772/api/live-feedback?path=/Users/me/Projects/app/app-alpha&service=web&sig=3f9a…
```

| Query key | Meaning |
|---|---|
| `path` | The worktree's path. The server finds the worktree by real path. |
| `service` | The service's name in `.maelstrom.yaml`. Each service keeps its own params. |
| `sig` | Hex HMAC-SHA256 of `path`, a NUL byte, and `service`. The key is `live-feedback.key` in the notebook root. |

- The path names the worktree, not its id: `_main` has the same id in every project.
- The URL comes from the notebook's `orchestrator_url:`. A `.env` value overrides it: see
  [environment.md](../reference/environment.md).
- No `path`, or no `service`, closes the socket with code `4404`.
- A `sig` that does not verify closes the socket with code `4403`. Only a URL that
  `mael env start` signed opens a socket, because a worktree path is easy to guess. The check
  runs before the path lookup, so an unsigned caller cannot learn which paths are real.
- A signed URL whose path names no worktree closes the socket with code `4404`.
- Each notebook has its own key. An orchestrator refuses a URL signed for another notebook.

A client must stay up when the socket does not. It reconnects with backoff, and treats each param
as off while the socket is down.

## Frames

Each frame is one JSON object.

| Direction | Frame | When |
|---|---|---|
| client → provider | `{type: "exposeParams", params}` | On each open, and on each change to the declarations. The newest one wins. |
| provider → client | `{type: "setParams", params}` | After each `exposeParams`, with every declared param. Then on each change, with the changed params. |
| client → provider | `{type: "setParams", params}` | The user changed a param in the page. |
| client → provider | `{type: "feedback", id, feedback}` | The user sent **Feedback**. |
| provider → client | `{type: "reply", id, ok, body}` | Once for each `feedback` frame. |

The server ignores a frame of a type it does not know.

## Params

A declaration names each param, with its label, its type and a description:

```json
{
  "type": "exposeParams",
  "params": {
    "jig": { "label": "Jig", "type": "boolean", "description": "Show the jig on each page" }
  }
}
```

- `boolean` is the one type. The server ignores a declaration of another type.
- A name is one word of letters, digits and `_`, so the marker can carry it as an attribute.
  `service` is the marker's own. The server ignores a declaration with another name.
- A param that was never set is off.
- Each service keeps its own values. A value outlives the socket, so a dev server that restarts
  gets its values back in the first `setParams`.
- The declarations last while the socket is open. A service with no open socket leaves
  `Worktree.liveFeedback`, and its switches go from the Dev env tab.

The Dev env tab draws one switch for each boolean param, named by its label. A press runs
`POST /api/worktrees/{id}/live-feedback/{service}/params` with `{params: {jig: true}}`.

An agent sets a param with a marker in its message:

```
<live-feedback jig="on">
<live-feedback service="web" jig="off">
```

With no `service`, the marker sets the param on each connected service that exposes it. Each
other attribute is a param, set to `on` or `off`. A marker with another value stays as text.

## Every change needs an agent

Live feedback is the dev env and the agent talking to each other. So the server refuses a change
when the worktree has no agent, off as well as on.

- A change from the user, in the UI or in the page, goes to each agent as one `jig.param_set`
  External event: "The user turned Jig on for web on feat/x."
- A change no agent takes is undone.
- A set to the current value changes nothing and tells nobody.
- A refused `setParams` from the page is answered with the stored values, so the page follows them.
- When the last agent of the worktree exits, every param turns off. Otherwise a param stays on with
  no agent to hear it, and no change could turn it off.
- An agent's own marker tells nobody. A marker in a replayed backlog sets nothing.

## Feedback

`feedback` carries a typed body. The server runs `worktree.feedback` with it and answers with a
`reply`. `body` is `{agentIds, refused}` when one agent or more took the message, else `{error}`.

| Type | Fields |
|---|---|
| `monkeypatch` | `css`, `note` (optional) |
| `instruction` | `text`, `element` (optional) |
| `pick` | `scope`, `element`, `note` (optional) |
| `controls` | `title`, `changes`, `css` (optional), `note` (optional) |

Any type can carry a `screenshot`: the name of a file the client wrote to `.drafts/jig/`. See
[orchestrator-server.md](orchestrator-server.md), "Commands", for how each type reaches the
agent.

## The jig as a client

The jig's plugin runs in the Vite dev server, not in the page:

1. On open, it sends `exposeParams` with `jig`.
2. On `setParams`, it reads `params.jig`, and tells every page to show or hide the overlay.
3. "Hide jig" in the page sends `setParams {jig: false}`.
4. Send in the page posts to the plugin, which writes the screenshot and sends `feedback`.

See [orchestrator-ui.md](orchestrator-ui.md), "The jig", for the overlay itself.
