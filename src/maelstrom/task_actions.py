"""Task lifecycle actions: fire Linear/Sentry status changes on transitions.

A task can carry ``pre-action`` / ``post-action`` frontmatter codes of the form
``<provider>.<verb>`` (e.g. ``linear.done``). When the task moves to
``in-progress`` (its ``pre_action`` fires) or ``done`` (its ``post_action``
fires), the matching provider command runs in-process against the issue id
resolved from the task's own id or its immediate parent.

This is the single place that knows action codes → command functions and how to
resolve the target ref, keeping ``session_cli`` / ``task_cli`` thin. Actions
never block a transition: :func:`run_action` swallows every failure and warns
loudly to stderr (no matching ref, API error, unknown code).
"""

import re

import click

_LINEAR_REF = re.compile(r"^linear\.([A-Z][A-Z0-9]*-\d+)$")
_SENTRY_REF = re.compile(r"^sentry\.(.+)$")


def _linear_set_status(status: str):
    def run(ref_id: str) -> None:
        from maelstrom import linear

        linear.set_issue_status(ref_id, status)

    return run


def _sentry_resolve(ref_id: str) -> None:
    from maelstrom import sentry

    sentry.resolve_issue(ref_id)


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


def run_action(task, code: str) -> None:
    """Run lifecycle action ``code`` for ``task``. Never raises — warns to stderr.

    A falsy ``code`` (no action configured) is a clean no-op. An unknown code, a
    task with no matching ``linear.``/``sentry.`` ref, or a runner that raises
    all warn loudly to stderr and run nothing further.
    """
    if not code:
        return
    entry = _ACTIONS.get(code)
    if entry is None:
        click.echo(f"warning: unknown task action {code!r} on {task.id}", err=True)
        return
    regex, runner = entry
    ref = resolve_ref(task, regex)
    if ref is None:
        click.echo(
            f"warning: action {code!r} on {task.id}: no matching "
            f"linear./sentry. ref in id or parent",
            err=True,
        )
        return
    try:
        runner(ref)
        click.echo(f"action {code} -> {ref} (task {task.id})", err=True)
    except Exception as e:
        click.echo(
            f"warning: action {code!r} on {task.id} failed: {e}", err=True
        )


# Destination status -> which task field selects the action to fire. Firing keys
# off the *destination* status (not how the move was invoked), so every path
# that reaches ``in-progress``/``done`` triggers the right action; other
# statuses map to no field and no-op cleanly.
_ACTION_FOR_STATUS = {
    "in-progress": "pre_action",
    "done": "post_action",
}


def move_with_actions(store, project, id, new_status, *, now=None):
    """``model.move``, then fire the task's pre/post action for this destination.

    The single chokepoint for status transitions that may fire lifecycle
    actions. Wrapping ``model.move`` here (rather than putting network calls in
    the pure model) guarantees both the explicit ``mael task status start/done``
    path and the launch / session-end paths trigger actions — keyed off the
    destination status. Returns the moved Task; action failures never block the
    move (:func:`run_action` swallows + warns).
    """
    from maelstrom import task as model

    moved = model.move(store, project, id, new_status, now=now)
    field = _ACTION_FOR_STATUS.get(new_status)
    if field:
        run_action(moved, getattr(moved, field))
    return moved
