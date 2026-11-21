@type:Task
Feature: Task
  Primary seam: `mael_domain.task.Task`
  Held today at: no predicate. The refusals of `validate_command` in the orchestrator server
    keep I2 to I6 for a create and an update. The task table keeps I1. `mael task update`
    in a terminal keeps I3, I4 and I6 only, and `mael task edit` keeps none of I2 to I6. A load
    of task blocks does not check the Follows or the mode that a block names.

  # The Status of a task is one of four values. `task.move` refuses any other, so no rule
  # states it. The code has a fifth value, `template`, until Template is a type: a move to it
  # or from it is accepted.

  @id:I1 @unit
  Rule: A task id is unique in its project

  @id:I2 @unit
  Rule: A task has a title

  @id:I3 @unit
  Rule: The Priority of a task is critical, high, medium or low

  @id:I4 @unit
  Rule: The mode of a task is plan, normal or auto

  @id:I5 @unit
  Rule: Each task that a todo or in-progress task follows exists

  @id:I6 @unit
  Rule: Each comm that a task links to exists

  @id:I7 @unit
  Rule: Each command sets the updated time of the task that it names, and keeps its created time
    # A task that a delete or a rename changes on the side keeps its updated time.
