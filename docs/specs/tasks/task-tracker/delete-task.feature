@type:Task
Feature: Delete task
  Primary seam: `mael_domain.task.delete`
  Held today at: R1 in `task.delete` and in `validate_command("task.delete")`. R2 in
    `validate_command` only: `mael task rm` in a terminal does not keep it. E rules in
    `task.delete`.
  Outer seams: `mael task rm`, `DELETE /api/tasks/{project}/{id}`

  @id:R1 @unit
  Rule: The task must exist
    Example: an unknown task
      When "zzzz" is deleted
      Then it is refused: no task zzzz

  @id:R2 @unit
  Rule: A task with a running agent is not deleted
    Example: a working agent
      Given an agent that works on the task "a"
      When "a" is deleted
      Then it is refused: the agent is running on a, stop it first

  @id:E3 @unit
  Rule: A task that followed the deleted task, and is not Done or cancelled, no longer follows it
    Example: one follower waits and one is Done
      Given "b" (todo) and "c" (Done) follow "a"
      When "a" is deleted
      Then "b" follows nothing
      And "c" still names "a"

  @id:E4 @unit
  Rule: A delete removes the task and changes its followers, or does neither

  @id:E5 @unit
  Rule: The deleted task no longer exists
    Example: a task with no followers
      Given the task "a"
      When "a" is deleted
      Then no task "a" exists
