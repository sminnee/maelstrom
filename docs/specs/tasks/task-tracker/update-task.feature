@type:Task
Feature: Update task
  Primary seam: `mael_domain.task.update`
  Held today at: R rules in `validate_command("task.update")`. E rules in `task.update`.
    `mael task follow` and `unfollow` go through the orchestrator server and keep each rule.
    `mael task update` in a terminal writes through `task.update` and keeps only R4 and R8.
    `mael task edit` writes through `edit_in_editor` and keeps no R rule.
  Outer seams: `mael task update`, `mael task follow`, `mael task unfollow`, `mael task edit`,
    `PATCH /api/tasks/{project}/{id}`, `mael comms link`

  @id:R1 @unit
  Rule: The task must exist
    Example: an unknown task
      When the title of "zzzz" is changed
      Then it is refused: no task zzzz

  @id:R2 @unit
  Rule: An update must change a field
    Example: no field
      When a task is updated with no field named
      Then it is refused: nothing to change

  @id:R3 @unit
  Rule: The title cannot be blank
    Example: spaces only
      When the title of a task is changed to "   "
      Then it is refused: a title is required

  @id:R4 @unit
  Rule: The Priority and the mode must be values that a task can have
    Example: an unknown mode
      When the mode of a task is changed to "yolo"
      Then it is refused: no mode yolo

  @id:R5 @unit
  Rule: A task cannot follow itself
    Example: its own id
      When the task "a" is changed to follow "a"
      Then it is refused: a task cannot follow itself

  @id:R6 @unit
  Rule: Each task that the task follows must exist
    Example: an unknown task
      When the task "a" is changed to follow "zzzz"
      Then it is refused: no task zzzz

  @id:R7 @unit
  Rule: Follows cannot make a cycle
    Example: two tasks
      Given the task "b" follows "a"
      When the task "a" is changed to follow "b"
      Then it is refused: that would make a cycle
    Example: three tasks
      Given "b" follows "a" and "c" follows "b"
      When the task "a" is changed to follow "c"
      Then it is refused: that would make a cycle

  @id:R8 @unit
  Rule: Each comm that the task newly links to must exist
    Example: an unknown comm
      When the comm link "c99" is added to a task
      Then it is refused: no comm c99

  @id:E9 @unit
  Rule: Only the fields that the caller names change
    Example: a new title
      Given a task with the title "Old" and the Priority high
      When its title is changed to "New"
      Then its title is "New" and its Priority is high

  @id:E10 @unit
  Rule: An update never changes the status of the task

  @id:E11 @unit
  Rule: An update never changes the id of the task
