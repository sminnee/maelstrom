@type:Template @unit
Feature: Create template
  Makes one Template. A Template never launches: maelstrom copies it into a new task on a
  schedule or on a Trigger. A Template takes its values as a new task does: see create-task.feature.

  @id:R1
  Rule: A Trigger must name its kind and its spec
    Example: a trigger with no kind
      Given the trigger "nightly.yml"
      When a template is created with it
      Then it is refused: the trigger is not "gh-action/<workflow>"

  @id:E2
  Rule: A new template is a Template, not a todo task

  # --- Code ---
  # Primary seam: `mael_domain.task.create_template`
  # Held today at: R1 in `validate_command("task.create")`. E2 in `task.create` with the status
  #   `template`.
  # Outer seams: `mael task add --template`, `POST /api/tasks`
