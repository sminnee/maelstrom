@type:Task @unit
Feature: Delete task
  Removes one task for good. The tasks that waited on it wait on it no longer.

  The task must exist: see task.feature.

  @id:R1
  Rule: A task with a running agent is not deleted
    Example: a working agent
      Given an agent that works on the task "a"
      When "a" is deleted
      Then it is refused: the agent is running on a, stop it first

  @id:E2
  Rule: A task that followed the deleted task no longer follows it, unless it is Done or cancelled
    Example: one follower waits and one is Done
      Given "b" (todo) and "c" (Done) follow "a"
      When "a" is deleted
      Then "b" follows nothing
      And "c" still names "a"

  @id:E3
  Rule: A delete removes the task and changes its followers, or does neither

  # --- Code ---
  # Primary seam: `mael_domain.task.delete`
  # Held today at: R1 in `validate_command("task.delete")` only: `mael task rm` in a terminal
  #   does not keep it. E rules in `task.delete`.
  # Outer seams: `mael task rm`, `DELETE /api/tasks/{project}/{id}`
