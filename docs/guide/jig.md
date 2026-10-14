# The jig

The **Jig** is an overlay on the pages of a worktree's dev servers. In it, you write CSS that
applies to the page at once, and you send that CSS or an instruction to the agent in the worktree.
[CONTEXT.md](../../CONTEXT.md) defines the jig and the words around it: **Monkeypatch**, **Pick**,
**Control set** and **Feedback**.

The jig comes from a Vite plugin, `vite-plugin-mael-jig`, on npm. A project adds it with one line
in each Vite config that serves pages.

## Add the jig to a project

Install the plugin as a dev dependency:

```bash
pnpm add -D vite-plugin-mael-jig
```

Add `maelJig()` to the app's Vite config:

```ts
// vite.config.ts
import { defineConfig } from 'vite';
import { maelJig } from 'vite-plugin-mael-jig';

export default defineConfig({
  plugins: [maelJig()],
});
```

Add the same line to each other Vite config that serves pages. A Ladle catalogue that has its own
config, such as `.ladle/vite.config.ts`, needs it too. A Ladle that loads the app's config gets
the jig from that config.

The plugin needs Vite 6 or later. It needs no options and no guard:

| Case                             | What the plugin does                    |
| -------------------------------- | --------------------------------------- |
| `vite build`                     | Nothing. The plugin applies to `serve`. |
| Vitest                           | Nothing. Vitest serves no page.         |
| `MAEL_FEEDBACK_URL` unset        | Injects the client, which stays hidden. |
| Outside a git checkout           | Warns once, and serves no jig.          |

`mael env start` sets `MAEL_FEEDBACK_URL` for each service when the notebook root's `config.yaml`
has `orchestrator_url:`. See [environment.md](../reference/environment.md). To set the URL in the
config, pass `maelJig({ providerUrl })`.

## Who can send

The jig's routes under `/__mael/` write draft files and send feedback to the agents. The plugin
refuses a `POST` or `PUT` from a browser page on another origin. It needs the overlay's
`x-mael-jig-client` header. A request that sends an `Origin` must send the dev server's own.

The plugin trusts any host that can reach the dev server. Such a host can load the page, so a
token in the page would not stop it. Bind `server.host` only where trusted hosts reach it, or
limit it with the tailnet ACL.

A DNS-rebinding page sends an `Origin` that matches its `Host`, so the `Origin` check does not
stop it. Over HTTP, `server.allowedHosts` stops it: never set it to `true`. Over HTTPS, Vite
skips that check, and the certificate stops it instead.

## A server in middleware mode

An Express or other Node server can run Vite in middleware mode. The plugin works there too. The
server must pass each page through `vite.transformIndexHtml()`, as an SPA (single-page app) setup
does. That call injects the jig's client.

## Check that it works

1. Start the service with `mael env start <service>`.
2. Start an agent in the worktree.
3. Open the Dev env tab in the orchestrator. Its toolbar has a "Jig" switch while an agent is in
   the worktree.
4. Turn the switch on. The `Jig` pill shows on the page.

When the switch does not show, the dev server did not reach the orchestrator. Check that
`MAEL_FEEDBACK_URL` is set in the service's environment. When the dev server's log says the
provider refused this URL, run `mael env restart <service>`.

When the switch is on and the pill does not show, check that the page loads
`/@id/__x00__virtual:mael-jig/client`.

## Use a local copy of the plugin

`mael self-update` builds `_main`'s copy of the plugin when the pull changes it, and registers it
with `pnpm link --global`. To use that copy in a project in place of the npm release:

```bash
pnpm link --global vite-plugin-mael-jig
```

The link is an override for one checkout. The next `pnpm install` in that project drops it and
restores the version in `package.json`. Run the link command again after each install.

A linked copy resolves `vite` from its own dependencies. A type check of the Vite config can then
report two `Plugin` types that do not match. The dev server is not affected.
