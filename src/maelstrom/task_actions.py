"""Task lifecycle actions: fire Linear/Sentry status changes on transitions.

A task can carry ``pre-action`` / ``post-action`` frontmatter codes of the form
``<provider>.<verb>`` (e.g. ``linear.done``). When the task moves to
``in-progress`` (its ``pre_action`` fires) or ``done`` (its ``post_action``
fires), the matching provider command runs in-process against the issue id
resolved from the task's own id or its immediate parent.

This is the single place that knows action codes → command functions and how to
resolve the target ref, keeping ``session_cli`` / ``task_cli`` thin. Actions
never block a transition: :func:`run_action` swallows every failure and passes
a warning line to the caller's ``warn`` (no matching ref, API error, unknown
code).
"""

import re
from collections.abc import Callable

_LINEAR_REF = re.compile(r"^linear\.([A-Z][A-Z0-9]*-\d+)$")
_SENTRY_REF = re.compile(r"^sentry\.(.+)$")


def _linear_set_status(status: str):
    def run(ref_id: str) -> str:
        from .integrations import linear

        return linear.set_issue_status(ref_id, status)

    return run


def _sentry_resolve(ref_id: str) -> str:
    from .integrations import sentry

    return sentry.resolve_issue(ref_id)


# Action code -> (provider-ref regex, runner). The regex both selects which ref
# (self/parent) the action targets and extracts the bare issue id from it.
_ACTIONS = {
    "linear.planned": (_LINEAR_REF, _linear_set_status("planned")),
    "linear.in-progress": (_LINEAR_REF, _linear_set_status("in-progress")),
    "linear.done": (_LINEAR_REF, _linear_set_status("done")),
    "sentry.resolve": (_SENTRY_REF, _sentry_resolve),
}


def resolve_ref(task, regex: re.Pattern) -> str | None:
    """Return the issue id from the task's own id or immediate parent, or None.

    Checks ``task.id`` first (a task literally named ``sentry.XYZ`` resolves to
    itself), then the immediate ``task.parent`` (the common
    ``linear.PROJ-XXX`` case) — matching the existing branch-derivation logic.
    No deep ancestor walk.
    """
    for candidate in (task.id, task.parent):
        if candidate:
            m = regex.match(candidate)
            if m:
                return m.group(1)
    return None


def run_action(task, code: str, *, warn: Callable[[str], None]) -> None:
    """Run lifecycle action ``code`` for ``task``. Never raises — calls ``warn``.

    A falsy ``code`` (no action configured) is a clean no-op. An unknown code, a
    task with no matching ``linear.``/``sentry.`` ref, or a runner that raises
    all pass a warning to ``warn`` and run nothing further. A runner that
    succeeds passes its result and a success line to ``warn`` too.
    """
    if not code:
        return
    entry = _ACTIONS.get(code)
    if entry is None:
        warn(f"warning: unknown task action {code!r} on {task.id}")
        return
    regex, runner = entry
    ref = resolve_ref(task, regex)
    if ref is None:
        warn(
            f"warning: action {code!r} on {task.id}: no matching "
            f"linear./sentry. ref in id or parent"
        )
        return
    try:
        warn(runner(ref))
        warn(f"action {code} -> {ref} (task {task.id})")
    except Exception as e:
        warn(f"warning: action {code!r} on {task.id} failed: {e}")


# Destination status -> which task field selects the action to fire. Firing keys
# off the *destination* status (not how the move was invoked), so every path
# that reaches ``in-progress``/``done`` triggers the right action; other
# statuses map to no field and no-op cleanly.
_ACTION_FOR_STATUS = {
    "in-progress": "pre_action",
    "done": "post_action",
}


async def move_with_actions(
    table, project, id, new_status, *, warn: Callable[[str], None], now=None
):
    """``model.move``, then fire the task's pre/post action for this destination.

    The single chokepoint for status transitions that may fire lifecycle
    actions. Wrapping ``model.move`` here (rather than putting network calls in
    the pure model) guarantees both the explicit ``mael task status start/done``
    path and the launch / session-end paths trigger actions — keyed off the
    destination status. Returns the moved Task; action failures never block the
    move (:func:`run_action` swallows + warns).
    """
    from . import task as model

    moved = await model.move(table, project, id, new_status, now=now)
    field = _ACTION_FOR_STATUS.get(new_status)
    if field:
        run_action(moved, getattr(moved, field), warn=warn)
    return moved
