@type:Template
Feature: Create template
  Primary seam: `mael_domain.task.create_template`
  Held today at: R rules in `validate_command("task.create")`. E rules in `task.create` with
    the status `template`.
  Outer seams: `mael task add --template`, `POST /api/tasks`

  @id:R1 @unit
  Rule: A Trigger must name its kind and its spec
    Example: a trigger with no kind
      Given the trigger "nightly.yml"
      When a template is created with it
      Then it is refused: the trigger is not "gh-action/<workflow>"

  @id:E2 @unit
  Rule: A new template is a Template, not a todo task
    Example: a template with a schedule
      When a template is created with the schedule "0 9 * * 1"
      Then the tasks of status todo do not hold it
      And the templates hold it with that schedule
