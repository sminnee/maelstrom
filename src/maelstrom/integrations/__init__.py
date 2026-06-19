"""Third-party service integrations (Linear, Sentry, UptimeRobot).

Each integration is CLI-coupled today; the shared private helpers
(``_auth``, ``_http``, ``_format``) collapse the secret-resolution,
urllib-wrapper, and time/status-formatting duplication that previously lived
in all three modules.
"""
