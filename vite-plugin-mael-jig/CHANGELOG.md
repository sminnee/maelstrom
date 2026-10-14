# Changelog

All notable changes to `vite-plugin-mael-jig` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this package
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Its version is its own, not
the `mael` CLI's. A change to the Live feedback frames the plugin sends is a major bump.

Anything a user would notice goes under `Unreleased` as it lands. `bin/publish-jig` refuses to
release while that section is empty, and retitles it to the version it is releasing as it tags.

## [Unreleased]

### Security

- **A page on another origin cannot reach the agents.** The plugin refuses a `POST` or `PUT` to
  `/__mael/*` that lacks the jig's `x-mael-jig-client` header, or whose `Origin` is not the dev
  server. Before, any page in the browser could send feedback to the agents or write
  `.drafts/monkeypatch.css`.

## [0.1.0] - 2026-10-13

### Added

- **The first release.** `maelJig()` in a Vite config shows the jig on each page of the dev
  server: a Monkeypatch editor, controls, and a message box that sends feedback to the agent in
  the worktree. It reads `MAEL_FEEDBACK_URL`, which `mael env start` sets. It does nothing in a
  build or under Vitest. Vite 6 and later.
