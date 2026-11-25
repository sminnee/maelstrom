@type:Task @unit
Feature: Update task
  Changes the fields of one task that the caller names: its title, its content, its Recipe, its
  Follows, its comm links. It does not move the task and does not rename it.

  Each value must be legal, and the task must exist: see task.feature.

  @id:R1
  Rule: An update must change a field
    Example: no field
      When a task is updated with no field named
      Then it is refused: nothing to change

  @id:E2
  Rule: Only the fields that the caller names change, and never the Status or the id
    Example: a new title
      Given a task with the title "Old" and the Priority high
      When its title is changed to "New"
      Then its title is "New" and its Priority is high
    Example: an empty value clears the field
      Given a task that follows "a"
      When its Follows is changed to an empty list
      Then it follows nothing

  # --- Code ---
  # Primary seam: `mael_domain.task.update`
  # Held today at: R1 in `validate_command("task.update")`. E2 in `task.update`.
  #   `mael task follow` and `unfollow` go through the orchestrator server.
  #   `mael task update` in a terminal writes through `task.update`, and `mael task edit` writes
  #   through `edit_in_editor`: neither keeps R1.
  # Outer seams: `mael task update`, `mael task follow`, `mael task unfollow`, `mael task edit`,
  #   `PATCH /api/tasks/{project}/{id}`, `mael comms link`
