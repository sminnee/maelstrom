# The jig

Your agent prompt's "Jig" section covers the reply loop: instructions, control sets and the `<live-feedback>` marker. This file covers the setup and the files behind it.

## Add the jig to a project

1. Install the plugin: `pnpm add -D vite-plugin-mael-jig`.
2. Add `maelJig()` to the `plugins` of each Vite config that serves pages:

   ```ts
   import { maelJig } from 'vite-plugin-mael-jig';
   // ...
   plugins: [react(), maelJig()],
   ```

3. When Ladle has its own config, such as `.ladle/vite.config.ts`, add `maelJig()` there too.
4. When the app runs Vite in middleware mode, check that the server passes each page through `vite.transformIndexHtml()`. The jig's client is injected there.

Pass no options and add no `VITEST` or `command` guard: the plugin reads `MAEL_FEEDBACK_URL`, and does nothing in a build or under Vitest.

## Check that it works

1. Restart the service with `mael env restart <service>`.
2. Fetch the page from its dev URL. It contains `virtual:mael-jig/client`.
3. Write `<live-feedback jig="on">`. The `Jig` pill shows on the page, and the Dev env tab's "Jig" switch is on.

When the page has no client script, check that the config change loaded. When the pill does not show, check that `MAEL_FEEDBACK_URL` is in the service's environment. `mael env start` sets it only when the notebook root's `config.yaml` has `orchestrator_url:`.

## Use `_main`'s copy of the plugin

`mael self-update` builds `_main/vite-plugin-mael-jig` and runs `pnpm link --global` there. To test an unreleased plugin change in a project, run `pnpm link --global vite-plugin-mael-jig` in the project's frontend directory. The next `pnpm install` drops the link. Do not commit the linked state: `package.json` keeps the npm version.

## The draft files

The jig and you share these files in the worktree's `.drafts/`:

| File | Written by | Holds |
|---|---|---|
| `monkeypatch.css` | the user and you | The Monkeypatch, and the wiring of each control. |
| `jig-controls.json` | you | The control set. The jig only reads it. |
| `jig-values.json` | the jig | `{var: value}` for each control the user set. |
| `jig/shot-<ms>.jpg` | the jig | The screenshot of each send. The newest is the page the user sees now. |

The jig applies each file as you write it. A page reload is not needed.

## Turn a confirmed Monkeypatch into source

Start this only after the user confirms the direction.

1. Read `monkeypatch.css` and `jig-values.json`.
2. Put each rule in the component's own stylesheet, on its own selector, not on the `[class*=…]` match the Monkeypatch used.
3. Replace each `--jig-*` variable with its value from `jig-values.json`, or with the project's design token that matches it.
4. Delete `.drafts/monkeypatch.css`, `.drafts/jig-controls.json` and `.drafts/jig-values.json`.
5. Check the page: it looks as it did with the Monkeypatch applied.
6. Write `<live-feedback jig="off">`, then commit the change.
