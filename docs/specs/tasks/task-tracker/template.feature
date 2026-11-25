@type:Template @unit
Feature: Template
  A Template holds a Recipe that maelstrom copies into a new task, on a schedule or on a Trigger.

  @id:I1
  Rule: A template is never Actionable

  # --- Code ---
  # Primary seam: `mael_domain.task.Template`
  # Held today at: no type. A template is a `Task` row with the status `template`. `is_actionable`
  #   and the default view of `mael task list` keep I1.
