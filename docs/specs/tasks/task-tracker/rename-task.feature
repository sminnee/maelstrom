@type:Task
Feature: Rename task
  Primary seam: `mael_domain.task.rename`
  Held today at: R1 to R3 and the E rules in `task.rename`. R4 in `mael task update --id`,
    which also refuses each in-progress task: an older form of R4 that no rule states.
    The orchestrator server has no rename command.
  Outer seams: `mael task update --id`

  @id:R1 @unit
  Rule: The task must exist
    Example: an unknown task
      When "zzzz" is renamed to "a"
      Then it is refused: no task zzzz

  @id:R2 @unit
  Rule: The new id must be free in the project
    Example: a taken id
      Given the tasks "a" and "b"
      When "a" is renamed to "b"
      Then it is refused: the task b already exists
    Example: the id that the task has
      Given the task "a"
      When "a" is renamed to "a"
      Then it is not refused, and nothing changes

  @id:R3 @unit
  Rule: The new id must have the form of a task id
    Example: a path
      When "a" is renamed to "../a"
      Then it is refused: unsafe task id

  @id:R4 @unit
  Rule: A task with a Live session is not renamed
    Example: a running session
      Given a Live session on the task "a"
      When "a" is renamed to "b"
      Then it is refused: a session is live on a

  @id:E5 @unit
  Rule: The renamed task keeps its status, its content, its Task log and its Task runs

  @id:E6 @unit
  Rule: Each todo or in-progress task that followed the old id follows the new id
    Example: one follower waits and one is done
      Given "b" (todo) and "c" (done) follow "a"
      When "a" is renamed to "x"
      Then "b" follows "x"
      And "c" still names "a"

  @id:E7 @unit
  Rule: Each todo or in-progress task whose Parent was the old id has the new id as its Parent
    Example: a child keeps its own id
      Given the task "a.1" with the Parent "a"
      When "a" is renamed to "x"
      Then the task "a.1" has the Parent "x" and the id "a.1"

  @id:E8 @unit
  Rule: A rename changes the task and each reference to it, or changes nothing

  @id:E9 @unit
  Rule: The task is found under the new id, and the old id names no task
    Example: a rename
      Given the task "a"
      When "a" is renamed to "x"
      Then the task "x" exists
      And no task "a" exists
