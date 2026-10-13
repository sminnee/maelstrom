# vite-plugin-mael-jig

The jig for [maelstrom](https://github.com/sminnee/maelstrom). The jig is an overlay on the pages
of a Vite dev server. In it, you write CSS that applies to the page at once, and you send it, or an
instruction, to the agent that works in the worktree.

## Use it

```bash
pnpm add -D vite-plugin-mael-jig
```

```ts
// vite.config.ts
import { maelJig } from 'vite-plugin-mael-jig';

export default defineConfig({
  plugins: [maelJig()],
});
```

Add the same line to each Vite config that serves pages, such as a separate Ladle config.

The plugin does nothing in a build or under Vitest. Without `MAEL_FEEDBACK_URL`, it injects the
jig, which stays hidden. `mael env start` sets the variable for each service.

See [the jig guide](https://github.com/sminnee/maelstrom/blob/main/docs/guide/jig.md) for the
setup, the check that it works, and the local override.

## Options

| Option        | Default                         | Meaning                                    |
| ------------- | ------------------------------- | ------------------------------------------ |
| `providerUrl` | `process.env.MAEL_FEEDBACK_URL` | The Live feedback provider's WebSocket URL |
