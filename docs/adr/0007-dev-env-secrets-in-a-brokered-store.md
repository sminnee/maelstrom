# Dev-env secrets live in a brokered Secret store

Status: accepted, 2026-11-28
Rollout: partial, 1 of 5

A project's secrets leave the `.env` files. Each **Secret** has one encrypted copy, in the
**Secret store** under the notebook root. A Service names the secrets it needs in
`.maelstrom.yaml`. The orchestrator server puts those values into that Service's process at start,
and into nothing else. You unlock the store once per server start, and the server holds the key in
its memory only.

## Why

A secret in the project root `.env` is plaintext. Maelstrom copies that file into each worktree, so
one secret has as many copies as the project has worktrees. Each Service gets each value. So does
each agent, because a worktree's agent daemon is a Service. An agent can also read the file.

The design stops three things: plaintext at rest, an agent that reads a secret, and copies with no
one place to rotate. It must not make dev work slower.

## The decisions

| Decision | Choice |
|---|---|
| Source of truth | Maelstrom's own store, one for each host. Not an outside password manager. |
| Reference, not value | `.maelstrom.yaml` holds the names. The server injects the values at spawn, as `op run`, `doppler run` and `aws-vault exec` do. |
| Least privilege | A Service gets only the secrets that it declares. |
| Who opens the store | The orchestrator server alone. `mael secret` is a client of its routes and has no local path. |
| Who spawns | The server starts each environment that declares secrets. A value then never passes through a process that an agent started. An environment with no declared secrets still starts from the CLI. |
| Values are write-only | No CLI command and no API route returns a value. An agent holds the CLI's Host key, so such a route gives each value to each agent. The UI reveals a value only after a Passkey sign-in. |
| Unlock | A passphrase, once per server start. The server writes the unlocked key to no file. A restart locks the store again. Running Services keep their values: a locked store stops only a new start. |
| Server memory | The server marks its process not dumpable. A process of the same user then cannot read `/proc/<pid>/mem` or attach a debugger. Linux only. |
| Names are not secret | `mael secret list` works on a locked store. An agent can see which names a Service needs and which are missing. |
| Scopes | A Secret has a project scope or the global scope. A project value wins over a global one. A worktree has no scope of its own. |
| Cipher | The `cryptography` package. scrypt turns the passphrase into a key. That key wraps a random store key. AES-256-GCM with the store key encrypts each value, bound to its scope and name. |
| Delegate keys | A Delegate key opens no secret route. |

## Considered options

- **Encrypted files in the repo**, as SOPS and dotenvx do. The key then has to reach each
  worktree, and an agent in the worktree can use it. Rejected: the user chose a host store.
- **An outside password manager as the source.** It adds a sign-in and a network call to each
  start. Rejected for now. The store's `resolve` is the place to add one.
- **The CLI opens the store.** An agent runs the CLI, so the unlocked key would be in a process
  that an agent started. Rejected.
- **The key in the OS keyring.** A process of the same user can ask the keyring for it, and an
  agent is such a process. Rejected.
- **A credential proxy.** The Service gets a placeholder, and a proxy outside the worktree puts in
  the real key for one allowed host. It needs a trusted certificate inside each Service. Deferred:
  see "The limit".

## The limit

An agent writes the code that a Service runs. An agent that changes that code to print a secret
gets the secret. So does a process of the same user that reads `/proc/<pid>/environ` of a Service.

An agent also holds the Host key. With it, the agent can replace or remove a Secret while the
store is unlocked. It cannot read one.

This design stops the common paths: `cat .env`, `env`, a transcript that quotes a file, and a
prompt injection that sends `.env` out. It does not stop a deliberate attack through a Service.
Only a credential proxy does. Grants limit the damage for the secrets that matter most.

## Consequences

- After each restart of the orchestrator server, such as `mael self update`, the user must unlock
  the store before an environment that declares secrets can start.
- A lost passphrase loses the store. The user sets each Secret again.
- Each host has its own store. A Remote host does not get a copy.
- The store of a dev orchestrator is under its own notebook root, so it never opens the production
  store.

## Rollout

Done for a line means: its PR is merged, and a test in `tests/` runs its behaviour against a real
orchestrator server.

- [x] The Secret store in the orchestrator server, and `mael secret unlock`, `set`, `list`, `rm`
- [ ] Services declare their secrets, and the server injects them. `mael secret import` moves
      values out of the project `.env`.
- [ ] The UI unlocks the store, shows missing secrets, and reveals a value after a Passkey
      sign-in. Sandbox deny rules cover the store's files.
- [ ] Maelstrom's own credentials move into the store: the integration keys, the Orchestrator
      token, the Host keys and the Agent token.
- [ ] Grants: a Secret whose value comes from a handshake script, with an approval that ends
      after a set time.
