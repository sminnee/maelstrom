# Dev environments

## Services and ports

- Declare each service in `.maelstrom.yaml` under `services:`, with a name for each port in its `ports:` list. A port named `FRONTEND` becomes `FRONTEND_PORT` in `.env`.
- Give the app's port a name with an `APP` or `FRONTEND` segment, such as `FRONTEND` or `LADLE_APP`. Only such a port gets an app URL.
- The service's process gets every `.env` value in its environment. `${VAR}` in `command:` and `env:` is substituted from `.env`.

```yaml
services:
  web:
    ports: [FRONTEND, FRONTEND_HMR]
    dir: web
    command: pnpm dev --port ${FRONTEND_PORT}
```

- Start a service with `mael env start <name>`. Never start a service by hand.
- Never hardcode a port. Several worktrees serve at once, so a port you pick collides with the next worktree.
- When the worktree's `.env` has no port for a service, run `mael env reset`, then `mael env restart`. A `.env` older than the service does not have the port.

## `.env` variables

Read the worktree's `.env` for the values. These rules are not in the file:

- Use `<NAME>_PORT`. Do not calculate a port from `PORT_BASE`: it holds the wrong base when the project has shared ports.
- Use `WORKTREE` for a key that is unique to the worktree. `WORKTREE_NUM` repeats after 16 worktrees.
- `DEV_HOST` and `DEV_SCHEME` are always present.
- `DEV_TLS_CERT` and `DEV_TLS_KEY` are present only when `DEV_SCHEME` is `https`.
- A server must accept the dev host: listen on every interface, and allow `DEV_HOST` as a `Host` header. For Vite: `server.host: true` and `server.allowedHosts: process.env.DEV_HOST ? [process.env.DEV_HOST] : undefined`.

## Service-to-service URLs

In a service's `env:`, write a URL to another service as:

```yaml
env:
  API_URL: ${DEV_SCHEME}://${DEV_HOST}:${API_PORT}
```

Do not use `localhost`. Under HTTPS, the certificate names only the dev host, so `https://localhost:<port>` fails.

## Serve HTTPS

Serve TLS when both `DEV_TLS_CERT` and `DEV_TLS_KEY` are set. Serve plain HTTP when they are not. Each app ends TLS itself, on its own port.

The user turns HTTPS on, with `dev_https:` in `~/.maelstrom/config.yaml`. Do not change that file. After the user turns it on, run `mael env reset`, then `mael env restart`. `mael env start` gets the certificate.

Vite (Ladle reads the same setting):

```ts
import { readFileSync } from 'node:fs';

const cert = process.env.DEV_TLS_CERT;
const key = process.env.DEV_TLS_KEY;

export default defineConfig({
  server: {
    https: cert && key ? { cert: readFileSync(cert), key: readFileSync(key) } : undefined,
  },
});
```

uvicorn:

```bash
uvicorn app:app --ssl-certfile "$DEV_TLS_CERT" --ssl-keyfile "$DEV_TLS_KEY"
```

Any other server: give it the certificate file and the key file from the two variables.

- HMR on its own port follows to `wss://`. Do not set an HMR host.
- A port serves one scheme. Once it serves TLS, `http://` on that port fails.

## App URL

Open the app at the app URL in the Environment section of `CLAUDE.local.md`. Keep its scheme and its host. To reach another service, change only the port. When there is no app URL, use `DEV_SCHEME`, `DEV_HOST` and the port from `.env`. Do not build a URL from `localhost` or from `http://`.
