"""Task lifecycle actions: fire Linear/Sentry status changes on transitions.

A task can carry ``pre-action`` / ``post-action`` frontmatter codes of the form
``<provider>.<verb>`` (e.g. ``linear.done``). When the task moves to
``in-progress`` (its ``pre_action`` fires) or finishes its build (its
``post_action`` fires: see :func:`_action_field`), the matching provider
command runs in-process against the issue id resolved from the task's own id or its immediate parent.

This is the single place that knows action codes → command functions and how to
resolve the target ref, keeping ``session_cli`` / ``task_cli`` thin. Actions
never block a transition: :func:`run_action` swallows every failure and passes
a warning line to the caller's ``warn`` (no matching ref, API error, unknown
code).
"""

import asyncio
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


#: The statuses before the build ends. A move from one of these to ``built``
#: or to a done status finishes the build.
_BEFORE_BUILT = ("todo", "in-progress")


def _action_field(old_status: str, new_status: str, moved) -> str | None:
    """The task field that selects the action this move fires, or None.

    ``pre_action`` fires on the move to ``in-progress``. ``post_action`` fires
    when the build ends: the move to ``built``, or a move straight to a done
    status for a task with no PR to wait on. A later ``built → merged`` move
    does not fire it again.
    """
    from . import task as model

    if new_status == model.STATUS_IN_PROGRESS:
        return "pre_action"
    if old_status in _BEFORE_BUILT and (
        new_status == model.STATUS_BUILT or model.is_done(moved)
    ):
        return "post_action"
    return None


async def move_with_actions(
    table, project, id, new_status, *, warn: Callable[[str], None], now=None
):
    """``model.move``, then fire the task's pre/post action for this move.

    The single chokepoint for status transitions that may fire lifecycle
    actions. Wrapping ``model.move`` here (rather than putting network calls in
    the pure model) guarantees both the explicit ``mael task status start/done``
    path and the launch / session-end paths trigger actions — keyed off the
    move: see :func:`_action_field`. Returns the moved Task; action failures
    never block the move (:func:`run_action` swallows + warns).
    """
    from . import task as model

    old_status = (await model.load(table, project, id)).status
    moved = await model.move(table, project, id, new_status, now=now)
    field = _action_field(old_status, moved.status, moved)
    if field:
        # An action makes blocking HTTP calls, such as Linear's. On a thread,
        # they do not stall the orchestrator server's loop for every client.
        await asyncio.to_thread(run_action, moved, getattr(moved, field), warn=warn)
    return moved
