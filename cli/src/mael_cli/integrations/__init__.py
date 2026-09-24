"""Third-party service integrations (Linear, Sentry, Slack, UptimeRobot).

Each integration is a model module plus a ``*_cli.py`` module that holds its
click group. The shared private helpers (``_auth``, ``_http``, ``_format``)
hold the secret resolution, the urllib wrapper and the time formatting.
"""
