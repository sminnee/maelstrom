@type:Task @unit
Feature: Rename task
  Gives one task a new id. Each task that names the old id then names the new one, so no Chain
  breaks.

  The new id must be legal, and the task must exist: see task.feature.

  @id:R1
  Rule: The new id must be free in the project
    Example: a taken id
      Given the tasks "a" and "b"
      When "a" is renamed to "b"
      Then it is refused: the task b already exists
    Example: the id that the task has
      Given the task "a"
      When "a" is renamed to "a"
      Then it is not refused, and nothing changes

  @id:R2
  Rule: A task with a Live session is not renamed

  @id:E3
  Rule: The renamed task keeps its Status, its content, its Task log and its Task runs

  @id:E4
  Rule: Each task that named the old id, as Follows or as Parent, names the new id, unless it is Done or cancelled
    Example: one follower waits and one is Done
      Given "b" (todo) and "c" (Done) follow "a"
      When "a" is renamed to "x"
      Then "b" follows "x"
      And "c" still names "a"
    Example: a child keeps its own id
      Given the task "a.1" with the Parent "a"
      When "a" is renamed to "x"
      Then the task "a.1" has the Parent "x" and the id "a.1"

  @id:E5
  Rule: A rename changes the task and each task that names it, or changes nothing

  # --- Code ---
  # Primary seam: `mael_domain.task.rename`
  # Held today at: R1 and the E rules in `task.rename`. R2 in `mael task update --id`, which
  #   also refuses each in-progress task: an older form of R2 that no rule states.
  #   The orchestrator server has no rename command.
  # Outer seams: `mael task update --id`
