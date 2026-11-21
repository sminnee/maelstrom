@type:Task
Feature: Set task status
  Primary seam: `mael_domain.task_actions.move_with_actions`
  Held today at: R rules in `validate_command("task.setStatus")` and in `task.move`. E rules
    in `task.move` and `move_with_actions`.
  Outer seams: `mael task status`, `POST /api/tasks/{project}/{id}/status`, the kanban drop

  @id:R1 @unit
  Rule: The task must exist
    Example: an unknown task
      When the status of "zzzz" is set to done
      Then it is refused: no task zzzz

  @id:R2 @unit
  Rule: The status must be one that a task can have
    Example: an unknown status
      When the status of a task is set to "paused"
      Then it is refused: no status paused

  @id:E3 @unit
  Rule: The task has the new status
    Example: a todo task is started
      Given a todo task
      When its status is set to in-progress
      Then the task is in-progress

  @id:E4 @unit
  Rule: A move to in-progress, and the end of the build, each run their Status action
    The end of the build is the move to built, or the move to Done of a task that was never
    built. A later move of a built task runs no Status action.
    The action acts on the issue that the task id names, else on the issue that its Parent names.
    Example: done on a Linear task
      Given a task with the Parent "linear.NORT-12" and the Status action "linear.done" for done
      When its status is set to done
      Then the Linear issue NORT-12 is set to done

  @id:E5 @unit
  Rule: A Status action that fails does not stop the move
    Example: the service refuses
      Given a task whose Status action for done fails
      When its status is set to done
      Then the task is Done, and the caller gets a warning

  @id:E6 @unit
  Rule: The caller gets each line that a Status action reports
    Example: done on a Linear task
      Given a task with the Status action "linear.done" for done
      When its status is set to done
      Then the caller gets the line that says which issue was set to done

  @id:E7 @unit
  Rule: A move to the status that the task has changes nothing in the task
    # The Status action of the move still runs.
    Example: todo to todo
      Given a todo task
      When its status is set to todo
      Then its updated time is as before

  @id:E8 @unit
  Rule: A move to done sets the first Status at which the task is Done
    Example: no Post-merge work
      Given an in-progress task with no Post-merge work
      When its status is set to done
      Then the task is merged
    Example: Post-merge work
      Given an in-progress task with Post-merge work
      When its status is set to done
      Then the task is published
    Example: a task that is Done
      Given a published task with no Post-merge work
      When its status is set to done
      Then the task is published, and its updated time is as before
