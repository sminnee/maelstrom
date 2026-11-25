@type:Task @unit
Feature: Set task status
  Moves one task to a new Status. A move can also tell an outside service, such as Linear, that
  the work started or ended: that is a Status action.

  The Status must be legal, and the task must exist: see task.feature.

  @id:E1
  Rule: A move sets the Status that the caller names, and "done" names the first Status at which the task is Done
    Scenario Outline: <case>
      Given <task>
      When its status is set to done
      Then the task is <status>

      Examples:
        | case                | task                                         | status    |
        | no Post-merge work  | an in-progress task with no Post-merge work  | merged    |
        | Post-merge work     | an in-progress task with Post-merge work     | published |
        | a task that is Done | a published task with no Post-merge work     | published |

  @id:E2
  Rule: A move to the Status that the task has changes nothing in the task
    # The Status action of the move still runs.

  # --- Status actions ---

  @id:E3
  Rule: A move to in-progress, and the end of the build, each run their Status action
    The end of the build is the move to built, or the move to Done of a task that was never
    built. A later move of a built task runs no Status action.
    The action acts on the issue that the task id names, else on the issue that its Parent names.
    Example: done on a Linear task
      Given a task with the Parent "linear.NORT-12" and the Status action "linear.done" for done
      When its status is set to done
      Then the Linear issue NORT-12 is set to done

  @id:E4
  Rule: A Status action that fails does not stop the move
    Example: the service refuses
      Given a task whose Status action for done fails
      When its status is set to done
      Then the task is Done, and the caller gets a warning

  @id:E5
  Rule: The caller gets each line that a Status action reports

  # --- Code ---
  # Primary seam: `mael_domain.task_actions.move_with_actions`
  # Held today at: E1 and E2 in `task.move`. E3 to E5 in `move_with_actions`.
  # Outer seams: `mael task status`, `POST /api/tasks/{project}/{id}/status`, the kanban drop
